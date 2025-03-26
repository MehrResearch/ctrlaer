"""
Demonstrates the use of the use_ms parameter in CtrlAer.run()
This example shows the difference between millisecond timing and raw clock tick timing.
"""
from ctrlaer import ON, OFF, CtrlAer
from time import sleep

FREQ = 100_000  # 100 kHz
ctrlaer = CtrlAer(sm_number=0, base_pin=7, n_pins=1, freq=FREQ)

def step_ms():
    # Use millisecond timing (default)
    for _ in range(5):
        yield ON, 200    # 200 ms square wave
        yield OFF, 200   # 200 ms off

ctrlaer.run(step_ms())  # use_ms=True by default
    

def step_ticks():
    # Specify in ticks (square wave counts)
    # 20000 ticks = 20000 * 1 / FREQ = 200 ms
    for _ in range(5):
        yield ON, 20000   # 20000 ticks square wave (200 ms)
        yield OFF, 20000  # 20000 ticks off (200 ms)

ctrlaer.run(step_ticks(), use_ms=False)  # Explicitly specify raw ticks
