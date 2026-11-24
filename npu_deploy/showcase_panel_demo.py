"""Synthetic stage/log fixture. No hardware, sockets, cameras or robot imports."""
import os
import signal
import sys
import time
from showcase_runner import WORKFLOWS

def event(kind,value):print('FLEXIFOLD_PANEL:'+os.environ['AIPC_SHOWCASE_PANEL_TOKEN']+':'+kind+':'+value,flush=True)
def main():
    signal.signal(signal.SIGINT,lambda *args:sys.exit(130))
    workflow=sys.argv[1]
    print('SIMULATION: 所有设备和检测结果均为模拟，不访问硬件。',flush=True)
    for key,title in WORKFLOWS[workflow][1]:
        if key != 'return':
            event('GATE',key)
            if sys.stdin.readline().strip():return 130
        event('STAGE',key)
        print('模拟阶段：'+title,flush=True);time.sleep(.4)
        if key in ('platform','cpu'):
            print('Model name: AMD Ryzen AI 9 HX 370 / SIMULATION',flush=True)
        if key in ('platform','rocm'):
            print('PyTorch: 2.11.0+rocm7.14.1\nROCm/HIP: 7.14.60850\nGPU: AMD Radeon 890M / SIMULATION\nGPU architecture: gfx1150\nCPU comparison: passed (SIMULATION)',flush=True)
        if key in ('platform','npu','run'):
            print('| [SIMULATION] | NPU Strix | aie2p |',flush=True)
        if key in ('npu','run'):
            print('Actually running on NPU 1\nNPU_FIRST_INFERENCE_COMPLETED: SIMULATION',flush=True)
        if key == 'run':
            print('TOWEL_FINISH_HOLD',flush=True)
        if key == 'return':
            print('TOWEL_RETURN_COMPLETE',flush=True)
        print('SIMULATION_STAGE_COMPLETE='+key,flush=True);time.sleep(.25)
    return 0
if __name__=='__main__':raise SystemExit(main())
