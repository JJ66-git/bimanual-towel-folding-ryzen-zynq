"""Bounded background recording of existing task buffers; no device constructors."""
import json
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import time

import numpy as np

ROLES = ('left', 'middle', 'right')


class RunRecorder:
    def __init__(self, directory, fps=10.):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fps = float(fps)
        if self.fps <= 0:
            raise ValueError('Recording fps must be positive')
        self.frames = Queue(maxsize=2)
        self.events = Queue(maxsize=4096)
        self.stop_sampling = threading.Event()
        self.stop_writing = threading.Event()
        self.sampler = None
        self.closed = False
        self.lock = threading.Lock()
        self.stats = dict(frames_submitted=0, frames_written=0, frames_dropped=0,
                          events_submitted=0, events_written=0, events_dropped=0,
                          sample_errors=0, write_errors=0, last_error=None)
        self.worker = threading.Thread(target=self._write, name='own_run_file_writer', daemon=True)
        self.worker.start()

    def _increment(self, key, error=None):
        with self.lock:
            self.stats[key] += 1
            if error is not None:
                self.stats['last_error'] = type(error).__name__ + ': ' + str(error)

    def record_event(self, kind, data):
        """The caller passes scalar copies; never retains or changes robot objects."""
        if self.closed:
            return False
        item = dict(kind=kind, wall_time_s=time.time(), monotonic_s=time.perf_counter(), **data)
        try:
            self.events.put_nowait(item)
        except Full:
            self._increment('events_dropped')
            return False
        self._increment('events_submitted')
        return True

    def sample_once(self, cameras):
        if self.closed or self.stop_sampling.is_set():
            return False
        if self.frames.full():
            self._increment('frames_dropped')
            return False
        try:
            wall, monotonic = time.time(), time.perf_counter()
            # read_latest does not consume the task's async_read event. Copies
            # isolate the recording worker from later cache replacements.
            images = {role: cameras[role].read_latest(max_age_ms=500).copy() for role in ROLES}
            if any(a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 3 for a in images.values()):
                raise ValueError('Expected uint8 HWC RGB task buffers')
            self.frames.put_nowait((wall, monotonic, images))
            self._increment('frames_submitted')
            return True
        except Full:
            self._increment('frames_dropped')
        except Exception as error:
            self._increment('sample_errors', error)
        return False

    def attach(self, cameras):
        if self.sampler is not None:
            raise RuntimeError('Recorder already attached')
        selected = {role: cameras[role] for role in ROLES}

        def sample():
            while not self.stop_sampling.is_set():
                self.sample_once(selected)
                self.stop_sampling.wait(1. / self.fps)

        self.sampler = threading.Thread(target=sample, name='own_run_cache_sampler', daemon=True)
        self.sampler.start()

    def _write(self):
        import cv2
        writers, shapes, last_images = {}, {}, None
        media_failed = False
        try:
            with (self.directory / 'robot_events.jsonl').open('w', buffering=1) as events, \
                    (self.directory / 'frame_times.jsonl').open('w', buffering=1) as times:
                while not self.stop_writing.is_set() or not self.events.empty() or not self.frames.empty():
                    for _ in range(256):
                        try:
                            event = self.events.get_nowait()
                        except Empty:
                            break
                        try:
                            events.write(json.dumps(event, allow_nan=False) + '\n')
                            self._increment('events_written')
                        except Exception as error:
                            self._increment('write_errors', error)
                    try:
                        wall, monotonic, images = self.frames.get(timeout=.03)
                    except Empty:
                        continue
                    if media_failed:
                        continue
                    try:
                        if not writers:
                            shapes = {role: image.shape for role, image in images.items()}
                            if len(set(shapes.values())) != 1:
                                raise ValueError('Three task views must have equal dimensions')
                            height, width = images['middle'].shape[:2]
                            for role in (*ROLES, 'three_views'):
                                size = (width * 3, height) if role == 'three_views' else (width, height)
                                writer = cv2.VideoWriter(str(self.directory / (role + '.avi')),
                                                         cv2.VideoWriter_fourcc(*'MJPG'), self.fps, size)
                                writers[role] = writer
                                if not writer.isOpened():
                                    raise OSError('Cannot create video for ' + role)
                            for role, image in images.items():
                                if not cv2.imwrite(str(self.directory / ('first_' + role + '.png')),
                                                   cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
                                    raise OSError('Cannot save first camera frame')
                        if any(image.shape != shapes[role] for role, image in images.items()):
                            raise ValueError('Task camera dimensions changed during recording')
                        for role, image in images.items():
                            writers[role].write(cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
                        writers['three_views'].write(cv2.cvtColor(np.hstack([images[r] for r in ROLES]),
                                                                  cv2.COLOR_RGB2BGR))
                        with self.lock:
                            index = self.stats['frames_written']
                        times.write(json.dumps(dict(video_frame_index=index, wall_time_s=wall,
                                                    monotonic_s=monotonic)) + '\n')
                        self._increment('frames_written')
                        last_images = images
                    except Exception as error:
                        self._increment('write_errors', error)
                        media_failed = True
                if last_images is not None:
                    for role, image in last_images.items():
                        if not cv2.imwrite(str(self.directory / ('last_' + role + '.png')),
                                           cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
                            raise OSError('Cannot save last camera frame')
        except Exception as error:
            self._increment('write_errors', error)
        finally:
            for writer in writers.values():
                writer.release()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_sampling.set()
        if self.sampler is not None:
            self.sampler.join(timeout=1.)
        self.stop_writing.set()
        self.worker.join(timeout=5.)
        with self.lock:
            summary = dict(self.stats)
        summary.update(writer_finished=not self.worker.is_alive(), fps=self.fps,
                       frame_time_basis='Host cache-sampling timestamps, not sensor exposure timestamps',
                       video_time_basis='Fixed fps; use frame_times.jsonl to retain real gaps/dropped frames',
                       action_recording_scope='Requested targets at original PIPERDual.send_action API; not raw CAN or execution acknowledgements',
                       raw_color_order='RGB; video encoding converts RGB to BGR once',
                       additional_camera_opens=0, additional_motor_commands=0)
        (self.directory / 'recording_summary.json').write_text(json.dumps(summary, indent=2) + '\n')


class Tee:
    def __init__(self, original, file, lock):
        self.original, self.file, self.lock = original, file, lock

    def write(self, value):
        result = self.original.write(value)
        try:
            with self.lock:
                self.file.write(value)
        except (OSError, ValueError):
            pass  # Log failure must not change a successful task write.
        return result

    def flush(self):
        self.original.flush()
        try:
            with self.lock:
                self.file.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self.original, name)
