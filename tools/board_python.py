"""Use the project's existing Python for Jupyter WebSocket operations, if needed."""
import importlib.util
import os
from pathlib import Path
import sys


def ensure_board_python(script):
    if importlib.util.find_spec('websocket') is not None:return
    root=Path(__file__).resolve().parents[1]
    python=root/'amd_hybrid_deploy_20261031/.venv/bin/python'
    if not python.is_file() or sys.executable==str(python):
        raise RuntimeError('Board management requires websocket-client in the existing project Python: '+str(python))
    os.execv(str(python),[str(python),str(Path(script).resolve()),*sys.argv[1:]])
