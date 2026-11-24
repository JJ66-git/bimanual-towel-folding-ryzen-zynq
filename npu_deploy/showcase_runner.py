"""PTY adapter for the existing stage-gated scripts. No robot API imports."""
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
import importlib.util
import codecs
import errno
import fcntl
import os
from pathlib import Path
import pty
import re
import secrets
import select
import signal
import subprocess
import struct
import sys
import termios
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
WORKFLOWS={
    'start':('start_showcase.sh',(('platform','复用准备结果'),('fpga','FPGA 服务'),('reset','双臂复位'),('run','混合任务'),('return','任务后归位'))),
    'reset':('reset_showcase.sh',(('reset','仅复位双臂'),)),
    'interact':('interact_showcase.sh',(('interactive','FPGA 互动与采集'),)),
    'prepare':('prepare_showcase.sh',(('budget','任务预算'),('devices','设备反馈'),('network','FPGA 网络'),('npu','NPU 预检'))),
    'amd':('check_amd_platform.sh',(('cpu','AMD CPU'),('rocm','ROCm 运算'),('npu','NPU 推理'))),
}
GATE_LABELS={'platform':'检查准备结果','fpga':'检查 FPGA 服务','reset':'确认复位 · 机械臂移动','interactive':'开始互动',
             'run':'确认运行 · 机械臂移动','return':'确认归位 · 机械臂移动',
             'budget':'开始准备检查','devices':'检查相机与双臂反馈',
             'rocm':'执行 ROCm 运算','network':'检查 FPGA 网络','npu':'开始 NPU 检查','cpu':'开始 AMD 自检'}


@lru_cache(maxsize=1)
def action_status_module():
    # PTY parsing also runs without a GUI bootstrap or Pillow installed.
    path=ROOT/'fpga_integration/fpga_code/fpga_workspace/host/action_status.py'
    spec=importlib.util.spec_from_file_location('runner_action_status',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@dataclass
class Status:
    workflow: str
    state: str = 'idle'
    stage: str = ''
    gate: str | None = None
    generation: int = 0
    returncode: int | None = None
    stages_done: list = field(default_factory=list)
    cpu: str = '等待检测'
    torch: str = '--'
    hip: str = '--'
    gpu: str = '等待本轮检测'
    arch: str = '--'
    rocm_verified: bool = False
    npu: str = '等待本轮检测'
    npu_partition: bool = False
    npu_inference: bool = False
    last_output_mono: float | None = None
    secret_input: bool = False
    interaction_verified: bool = False
    interaction_server_ready: bool = False
    fpga_task_end: dict | None = None
    operator_outcome: str | None = None
    authority_status: dict | None = None
    authority_session: int | None = None
    authority_fault: bool = False
    reset_phase: str = ''
    reset_display: dict | None = None
    dropped_lines: int = 0
    fold_finish_held: bool = False
    returned_home: bool = False


class Session:
    def __init__(self,workflow='start',*,command=None,environment=None,output_dir=None):
        if workflow not in WORKFLOWS:raise ValueError('Invalid workflow')
        self.status=Status(workflow)
        self.command=command
        self.environment=environment or {}
        self.token=secrets.token_hex(16)
        self.stop_requested=False
        self.lines=deque(maxlen=1200)
        self.pending_lines=deque(maxlen=300)
        self.lock=threading.RLock()
        self.process=None;self.master=None;self.worker=None
        self.finished=threading.Event()
        self.output_dir=Path(output_dir) if output_dir else ROOT/'npu_deploy/artifacts/launcher'/str(time.time_ns())
        self.log_path=self.output_dir/'console.log'

    def start(self):
        with self.lock:
            if self.status.state!='idle':raise RuntimeError('Session already started')
            self.status.state='starting'
        self.output_dir.mkdir(parents=True,exist_ok=False)
        master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack("HHHH",40,140,0,0))
        env=os.environ.copy();env.update(self.environment)
        env.update(AIPC_SHOWCASE_PANEL_TOKEN=self.token,LC_ALL='C',PYTHONUNBUFFERED='1')
        command=self.command or ['bash',str(ROOT/'npu_deploy'/WORKFLOWS[self.status.workflow][0])]
        try:
            self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',*command],
                stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True,cwd=ROOT)
        except Exception:
            os.close(master);raise
        finally:os.close(slave)
        self.master=master
        self.worker=threading.Thread(target=self._read,name='showcase-console',daemon=True)
        self.worker.start()

    def _parse_line(self,line):
        line=ANSI.sub('',line).rstrip('\r')
        prefix='FLEXIFOLD_PANEL:'+self.token+':'
        with self.lock:
            self.status.last_output_mono=time.monotonic()
            if line.startswith(prefix):
                if self.stop_requested:return
                fields=line[len(prefix):].split(':',1)
                if len(fields)==2:
                    kind,value=fields
                    allowed={key for key,_ in WORKFLOWS[self.status.workflow][1]}
                    if value in allowed:
                        if kind=='GATE':
                            old=self.status.stage
                            if old and old!=value and old not in self.status.stages_done:self.status.stages_done.append(old)
                            self.status.gate=value;self.status.generation+=1;self.status.state='waiting'
                        elif kind=='STAGE':
                            old=self.status.stage
                            if old and old!=value and old not in self.status.stages_done:self.status.stages_done.append(old)
                            self.status.stage=value;self.status.gate=None;self.status.state='running'
                return
            if line.startswith('FLEXIFOLD_PANEL:'):return
            self.lines.append(line)
            if len(self.pending_lines)==self.pending_lines.maxlen:self.status.dropped_lines+=1
            self.pending_lines.append(line)
            if line.startswith('Model name:'):self.status.cpu=line.split(':',1)[1].strip()
            for prefix,key in (('PyTorch:','torch'),('ROCm/HIP:','hip'),('GPU:','gpu'),('GPU architecture:','arch')):
                if line.startswith(prefix):setattr(self.status,key,line.split(':',1)[1].strip())
            if line.startswith('CPU comparison: passed'):self.status.rocm_verified=True
            if self.status.workflow=='reset' or self.status.stage=='reset':
                if line.startswith('RESET_START_VERSION='):self.status.reset_phase='preparing'
                elif line.startswith('Moving slowly.'):self.status.reset_phase='moving'
                elif line=='RESET_EXECUTION_COMPLETE':self.status.reset_phase='ended'
            if line.startswith('RESET_DISPLAY='):
                import json
                try:
                    receipt=json.loads(line.split('=',1)[1]);phase=receipt.get('phase')
                    if (phase in ('RESET_DONE','RESET_FAILED','RESET_STOPPED')
                            and receipt.get('motion_permission') is False and receipt.get('physical_result_verified') is False
                            and isinstance(receipt.get('receipts'),list) and all(isinstance(r,dict) for r in receipt['receipts'])):
                        self.status.reset_display=receipt
                        self.status.reset_phase={'RESET_DONE':'ended','RESET_FAILED':'error','RESET_STOPPED':'stopped'}[phase]
                except (ValueError,TypeError,AttributeError):pass
            if re.match(r'^\|.*NPU.*\|\s*$',line):
                cells=[part.strip() for part in line.strip('| ').split('|')]
                self.status.npu=' · '.join(cells[1:3]) if len(cells)>=3 else line.strip('| ')
            if re.search(r'Actually running on NPU\s+[1-9]\d*',line) or line.startswith('NPU_PARTITION_CONFIRMED:'):
                self.status.npu_partition=True
            if line.startswith(('NPU_FIRST_INFERENCE_COMPLETED:','NPU_CHECK_PASSED:','NPU_QUICKTEST_PASSED:')):
                self.status.npu_inference=True
            if line.startswith('BOOTH_B35_READY:'):
                self.status.interaction_verified=True
            if line.startswith('http://127.0.0.1:8766/') and self.status.interaction_verified:
                self.status.interaction_server_ready=True
            if line.startswith(('FPGA_AUTHORITY_DISPLAY=','FPGA_AUTHORITY_DECISION=')):
                import json
                try:
                    receipt=json.loads(line.split('=',1)[1]);row=receipt.get('status');session=receipt.get('session')
                    if type(session) is int and 1<=session<=0xfffffffe:
                        action_status_module().presentation(row)
                        prior=self.status.authority_status
                        if (session!=self.status.authority_session or prior is None or
                            row['target_sequence']>=prior['target_sequence'] and
                            (prior['stage'] not in ('denied','dispatch_unknown','error') or row['stage'] in ('denied','dispatch_unknown','error','ended','stopped','exiting'))):
                            if session!=self.status.authority_session:self.status.authority_fault=False
                            self.status.authority_session=session;self.status.authority_status=dict(row)
                            if row['stage'] in ('denied','dispatch_unknown','error'):self.status.authority_fault=True
                except (ValueError,TypeError,KeyError):pass
            if line.startswith('FPGA_TASK_END='):
                import json
                try:
                    receipt=json.loads(line.split('=',1)[1])
                    if (isinstance(receipt,dict) and receipt.get('origin')=='HOST'
                            and receipt.get('address')==['192.168.2.99',9009]
                            and type(receipt.get('session')) is int and 1<=receipt['session']<=0xFFFFFFFE
                            and receipt.get('phase') in ('ENDED','FAILED','STOPPED')):
                        self.status.fpga_task_end=receipt
                except (ValueError,TypeError):pass
            if (line == 'TOWEL_FINISH_HOLD' and self.status.workflow == 'start'
                    and self.status.stage == 'run' and not self.stop_requested):
                self.status.fold_finish_held = True
            if (line == 'TOWEL_RETURN_COMPLETE' and self.status.workflow == 'start'
                    and self.status.stage == 'return' and not self.stop_requested):
                self.status.returned_home = True

    def _read(self):
        decoder=codecs.getincrementaldecoder('utf-8')(errors='replace');buffer=''
        try:
            with self.log_path.open('w',encoding='utf-8',buffering=1) as log:
                while True:
                    try:data=os.read(self.master,65536)
                    except OSError as error:
                        if error.errno==errno.EIO:break
                        raise
                    if not data:break
                    chunk=decoder.decode(data)
                    log.write(chunk)
                    buffer+=chunk
                    while '\n' in buffer:
                        line,buffer=buffer.split('\n',1);self._parse_line(line)
                    if len(buffer)>16384:
                        self._parse_line(buffer[:16384]);buffer=buffer[16384:]
                buffer+=decoder.decode(b'',final=True)
                if buffer:self._parse_line(buffer)
            code=self.process.wait()
            with self.lock:
                self.status.returncode=code;self.status.gate=None
                self.status.state='stopped' if self.stop_requested or code in (130,-signal.SIGINT) else 'finished' if code==0 and not self.status.authority_fault else 'failed'
                if code==0 and not self.status.authority_fault and self.status.stage and self.status.stage not in self.status.stages_done:
                    self.status.stages_done.append(self.status.stage)
        except Exception as error:
            self._parse_line('PANEL_IO_ERROR='+str(error))
            # Never abandon a possibly moving child after a console failure.
            self.stop()
            code=self.process.wait()
            with self.lock:
                self.status.state='io_error';self.status.gate=None
                self.status.returncode=code or 1
        finally:
            if self.master is not None:
                try:os.close(self.master)
                except OSError:pass
            self.finished.set()

    def snapshot(self):
        from dataclasses import replace
        with self.lock:
            value=replace(self.status,stages_done=self.status.stages_done.copy())
        if self.master is not None and not self.finished.is_set():
            try:value.secret_input=not bool(termios.tcgetattr(self.master)[3]&termios.ECHO)
            except (OSError,termios.error):pass
        return value

    def drain(self):
        with self.lock:
            lines=list(self.pending_lines);self.pending_lines.clear()
        return lines

    def confirm(self,generation):
        with self.lock:
            if self.status.state!='waiting' or self.status.gate is None or generation!=self.status.generation:return False
            if self.process is None or self.process.poll() is not None:return False
            # Consume this gate before writing, so a double click cannot queue a future Enter.
            self.status.gate=None;self.status.state='running'
            try:os.write(self.master,b'\n')
            except OSError:return False
            return True

    def authenticate(self,value):
        if '\n' in value or '\r' in value:return False
        # Only a non-echo PTY prompt accepts this input. It never enters logs.
        with self.lock:
            if self.process is None or self.process.poll() is not None:return False
            try:
                if termios.tcgetattr(self.master)[3]&termios.ECHO:return False
                os.write(self.master,value.encode()+b'\n');return True
            except (OSError,termios.error):return False

    def stop(self):
        with self.lock:
            if self.process is None or self.process.poll() is not None:return
            self.stop_requested=True
            self.status.state='stopping';self.status.gate=None
            try:os.killpg(self.process.pid,signal.SIGINT)
            except ProcessLookupError:pass


class TerminalBridge:
    """Optional real terminal input. Parent echo is off; the child PTY owns echo."""
    def __init__(self,session):self.session=session;self.saved=None;self.done=threading.Event();self.thread=None
    def start(self):
        if not sys.stdin.isatty():return
        import tty
        self.fd=sys.stdin.fileno();self.saved=termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.thread=threading.Thread(target=self._input,daemon=True);self.thread.start()
    def _input(self):
        while not self.done.is_set() and not self.session.finished.is_set():
            try:
                readable,_,_=select.select([self.fd],[],[],.1)
                if readable:
                    data=os.read(self.fd,1024)
                    if not data:break
                    # Terminal is the original workflow: operator keystrokes go verbatim.
                    snapshot=self.session.snapshot()
                    if snapshot.gate and data and not data.strip(b'\r\n'):
                        self.session.confirm(snapshot.generation)
                    elif self.session.master is not None:os.write(self.session.master,data)
            except (OSError,ValueError):break
    def close(self):
        self.done.set()
        if self.thread:self.thread.join(timeout=.3)
        if self.saved is not None:
            try:termios.tcsetattr(self.fd,termios.TCSADRAIN,self.saved)
            except (OSError,termios.error):pass
            self.saved=None


def child_main(command):
    # Done after exec in a fresh session, never in preexec_fn of a threaded GUI.
    fcntl.ioctl(0,termios.TIOCSCTTY,0)
    signal.signal(signal.SIGINT,signal.SIG_DFL)
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':
    if sys.argv[1:2]==['--child'] and len(sys.argv)>2:child_main(sys.argv[2:])
    else:raise SystemExit('Internal PTY runner; use showcase_panel.py')
