"""Manual episode collection and durable local storage; no motion commands."""
import base64
import bisect
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import threading
import time
import math

SAMPLE_HZ = 25
EPISODE_ID = re.compile(r'ep_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}\Z')


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class Collector:
    def __init__(self, leader, directory):
        self.leader = leader
        self.directory = Path(directory).resolve()
        self.lock = threading.RLock()
        self.current = None

    def _sample(self, source, elapsed_ms):
        if source == 'piper_leader_commands':
            return self.leader.snapshot() if self.leader else None
        t = elapsed_ms / 1000
        return dict(joint_deg=[0, 14*(1-math.cos(t)), -5*(1-math.cos(t)), 0, 0, 0],
                    gripper_mm=30+24*math.cos(t*1.5))

    def start(self, instruction, source='piper_leader_commands'):
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 500:
            raise ValueError('请填写不超过 500 字的任务指令。')
        if source not in ('piper_leader_commands', 'synthetic_rehearsal'):
            raise ValueError('未知的数据来源。')
        with self.lock:
            if self.current and self.current['state'] == 'recording':
                raise RuntimeError('已有一条数据正在采集，请先结束并保存。')
            initial = self._sample(source, 0)
            if initial is None:
                raise ValueError('主臂数据尚未就绪，请检查连接。')
            now = datetime.now().astimezone()
            episode_id = 'ep_' + now.strftime('%Y%m%d_%H%M%S_') + secrets.token_hex(4)
            group = 'episodes' if source == 'piper_leader_commands' else 'rehearsals'
            path = self.directory / group / episode_id
            path.mkdir(parents=True)
            episode = dict(episode_id=episode_id, source=source, instruction=instruction.strip(),
                           started_at=now.isoformat(), state='recording', error=None,
                           start=time.monotonic(), path=path, stop=threading.Event(),
                           samples=[], frames=[], duration_ms=0, training_ready=False)
            self.current = episode
            try:
                (path / 'frames').mkdir()
                (path / 'instruction.txt').write_text(episode['instruction']+'\n', encoding='utf-8')
                (path / 'samples.jsonl').touch()
                (path / 'frames.jsonl').touch()
                self._append(episode, initial, 0)
                self._manifest(episode)
            except OSError:
                episode['state'] = 'save_error'
                raise
            thread = episode['thread'] = threading.Thread(target=self._run, args=(episode,), daemon=True)
            thread.start()
            return self._summary(episode)

    def _append(self, episode, sample, elapsed):
        row = dict(t_ms=round(elapsed, 3), **sample)
        with (episode['path'] / 'samples.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
        episode['samples'].append(row)

    def _run(self, episode):
        index = 1
        state, error = 'complete', None
        try:
            while not episode['stop'].wait(max(0, episode['start'] + index/SAMPLE_HZ - time.monotonic())):
                elapsed = (time.monotonic() - episode['start']) * 1000
                sample = self._sample(episode['source'], elapsed)
                if sample is None:
                    state, error = 'interrupted', '主臂数据中断，已保存收到的片段。'
                    break
                with self.lock:
                    if episode['state'] != 'recording': return
                    self._append(episode, sample, elapsed)
                # Do not generate a burst of duplicate rows after a delayed scheduler tick.
                index = max(index+1, int((time.monotonic()-episode['start'])*SAMPLE_HZ)+1)
        except Exception as exc:
            state, error = 'save_error', '写入采集数据失败：' + str(exc)
        with self.lock:
            if episode.get('shutdown'):
                state, error = 'interrupted', '采集服务退出，已保留收到的片段。'
            episode['state'], episode['error'] = state, error
            episode['duration_ms'] = round((time.monotonic()-episode['start'])*1000, 3)
            episode['ended_at'] = datetime.now().astimezone().isoformat()
            try:
                self._alignment(episode)
                self._manifest(episode)
            except OSError as exc:
                episode['state'], episode['error'] = 'save_error', '结束保存失败：' + str(exc)

    def _summary(self, episode):
        return dict(episode_id=episode['episode_id'], source=episode['source'],
                    instruction=episode['instruction'], state=episode['state'], error=episode['error'],
                    duration_ms=round((time.monotonic()-episode['start'])*1000, 3) if episode['state']=='recording' else episode['duration_ms'],
                    sample_count=len(episode['samples']), frame_count=len(episode['frames']),
                    nominal_hz=SAMPLE_HZ, training_ready=False,
                    saved_dir=str(episode['path']),
                    last_sample=episode['samples'][-1] if episode['samples'] else None)

    def status(self):
        with self.lock:
            return self._summary(self.current) if self.current else dict(state='idle')

    def _manifest(self, episode):
        missing = ['follower_measured_state', 'other_camera_views', 'training_format_conversion']
        if not episode['frames']: missing.append('camera_images')
        value = dict(schema='flexifold_raw_demonstration_v1', **self._summary(episode),
                     started_at=episode['started_at'], ended_at=episode.get('ended_at'),
                     action_semantics='right_leader_command_not_follower_feedback',
                     action_names=['right_joint_{}.command'.format(i) for i in range(1, 7)] + ['right_gripper.command'],
                     units=dict(joint_deg='degree', gripper_mm='millimeter', t_ms='millisecond'),
                     clock='host_monotonic', frame_timing='host_receipt_time_not_exposure_time',
                     alignment='nearest_action_within_200ms; not hardware synchronized',
                     missing_for_training=missing)
        write_json(episode['path'] / 'metadata.json', value)
        (episode['path'] / 'README.txt').write_text(
            'FlexiFold 原始示范采集\n任务：'+episode['instruction']+'\n'
            'instruction.txt: 语言任务；samples.jsonl: 带时间戳的右主臂指令；\n'
            'frames/: 本次相机 JPEG；frames.jsonl: 图像到达时间；alignment.jsonl: 近邻对应。\n'
            '动作单位为度、毫米；图像时间是服务器接收时间，不是曝光同步时间。\n'
            '这是原始采集素材；后续需筛选、校准、补齐从臂反馈及所需视角并转换训练格式。\n'
            '合成排练独立存于 rehearsals，不作为真实示范。\n', encoding='utf-8')

    def _alignment(self, episode):
        times = [row['t_ms'] for row in episode['samples']]
        with (episode['path'] / 'alignment.jsonl').open('w', encoding='utf-8') as out:
            for frame in episode['frames']:
                pos = bisect.bisect_left(times, frame['t_ms'])
                choices = [i for i in (pos-1, pos) if 0 <= i < len(times)]
                nearest = min(choices, key=lambda i:abs(times[i]-frame['t_ms'])) if choices else None
                delta = None if nearest is None else frame['t_ms'] - times[nearest]
                valid = delta is not None and abs(delta) <= 200
                out.write(json.dumps(dict(frame=frame['file'], frame_t_ms=frame['t_ms'],
                    sample_index=nearest if valid else None, delta_ms=delta,
                    matched=valid, timing='host_receipt_nearest_neighbor'))+'\n')

    def add_frame(self, episode_id, encoded, client_elapsed_ms=None):
        # This route accepts camera JPEGs only, never a path supplied by the browser.
        if not isinstance(encoded, str) or len(encoded) > 400000:
            raise ValueError('图像请求过大或格式错误。')
        try: jpeg = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc: raise ValueError('图像编码错误。') from exc
        if not 4 <= len(jpeg) <= 300000 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
            raise ValueError('需要 JPEG 图像。')
        if client_elapsed_ms is not None and (type(client_elapsed_ms) not in (int, float) or not math.isfinite(client_elapsed_ms) or client_elapsed_ms < 0):
            raise ValueError('相机相对时间无效。')
        with self.lock:
            episode = self.current
            if not episode or episode['episode_id'] != episode_id or episode['state'] != 'recording':
                raise RuntimeError('这条采集已经结束或编号不匹配。')
            elapsed = round((time.monotonic()-episode['start'])*1000, 3)
            relative = 'frames/{:06d}.jpg'.format(len(episode['frames']))
            (episode['path'] / relative).write_bytes(jpeg)
            row = dict(file=relative, t_ms=elapsed, browser_elapsed_ms=client_elapsed_ms,
                       timing='host_receipt', camera='browser_selected_camera')
            with (episode['path'] / 'frames.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(row)+'\n')
            episode['frames'].append(row)
            return dict(frame_count=len(episode['frames']))

    def stop(self, episode_id):
        with self.lock:
            episode = self.current
            if not episode or episode['episode_id'] != episode_id:
                raise ValueError('采集编号不匹配。')
            episode['stop'].set()
            thread = episode['thread']
        thread.join(timeout=5)
        if thread.is_alive(): raise RuntimeError('数据还在保存，请稍后再次结束。')
        with self.lock:
            return dict(**self._summary(episode), samples=list(episode['samples']), frames=list(episode['frames']))

    def episodes(self):
        result = []
        for group in ('episodes', 'rehearsals'):
            for path in (self.directory / group).glob('ep_*/metadata.json'):
                if not EPISODE_ID.fullmatch(path.parent.name): continue
                try: result.append(json.loads(path.read_text(encoding='utf-8')))
                except (OSError, ValueError): continue
        return sorted(result, key=lambda row:row.get('started_at',''), reverse=True)[:20]

    def close(self):
        with self.lock:
            episode = self.current
            if not episode or episode['state'] != 'recording': return
            episode['shutdown'] = True
            episode['stop'].set()
        episode['thread'].join(timeout=5)
