"""All checkouts of this physical rig share the original operation lock."""
import json
from pathlib import Path
from contextlib import contextmanager
import fcntl
import os


def operation_lock_path(project=None):
    project=Path(project or Path(__file__).resolve().parents[1])
    baseline=project.parent/'baseline/source_files.json'
    if baseline.is_file():
        origin=Path(json.loads(baseline.read_text())['source_workspace'])
        return origin/project.name/'npu_deploy/artifacts/showcase/operation.lock'
    return project/'npu_deploy/artifacts/showcase/operation.lock'


@contextmanager
def operation_lock(project=None):
    path=operation_lock_path(project);path.parent.mkdir(parents=True,exist_ok=True)
    inherited=False
    try:
        a=os.fstat(9);b=path.stat();inherited=(a.st_dev,a.st_ino)==(b.st_dev,b.st_ino)
    except OSError:pass
    if inherited:
        fcntl.flock(9,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield path  # The parent shell retains ownership; do not unlock fd 9.
    else:
        with path.open('a+b') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('任务或互动仍在运行，不能并发操作板卡。')
            yield path


if __name__=='__main__':print(operation_lock_path())
