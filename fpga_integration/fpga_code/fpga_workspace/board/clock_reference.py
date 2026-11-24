"""Match PYNQ 2.7's clock calculator to the board device tree; never changes a PLL."""
from pathlib import Path


def configure_reference():
    from pynq import Clocks
    path=Path('/sys/firmware/devicetree/base/amba/slcr@f8000000/clkc@100/ps-clk-frequency')
    data=path.read_bytes()
    if len(data)!=4:raise RuntimeError('Invalid PS reference clock device-tree property')
    hz=int.from_bytes(data,'big')
    if not 10000000<=hz<=100000000:raise RuntimeError('Implausible PS clock reference')
    instance=Clocks._instance
    if type(instance).__name__!='_ClocksZynq' or not hasattr(instance,'_ref_clk_mhz'):
        raise RuntimeError('Unsupported PYNQ clock implementation; explicit target validation required')
    previous=instance._ref_clk_mhz
    # Target-specific compatibility with the installed PYNQ 2.7 Zynq driver.
    # Only the software calculation reference is corrected. No MMIO write here.
    instance._ref_clk_mhz=hz/1000000.0
    return {'device_tree_hz':hz,'previous_driver_ref_mhz':previous,
            'driver_ref_mhz':instance._ref_clk_mhz,'fclk0_mhz':Clocks.fclk0_mhz}


def verify_fclk0(expected=50.0):
    from pynq import Clocks
    actual=Clocks.fclk0_mhz
    if abs(actual-expected)>.1:raise RuntimeError('Wrong physical FCLK0: {} MHz'.format(actual))
    return actual
