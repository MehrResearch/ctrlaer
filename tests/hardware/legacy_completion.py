"""Run on an otherwise idle Tiny 2040; GP8 is not connected on that board."""
from ctrlaer import CtrlAer, HIGH, LOW
from time import ticks_us, ticks_diff
from machine import Pin

controller = CtrlAer(0, 8, 1, freq=100000)
try:
    controller.sm  # Assemble/initialise before measuring execution time.
    start = ticks_us()
    controller.run([(HIGH, 200), (LOW, 1)])
    controller.block()
    elapsed = ticks_diff(ticks_us(), start)
    assert elapsed >= 200000, elapsed
    assert Pin(8).value() == 0
    print({'check': 'legacy_completion', 'elapsed_us': elapsed, 'passed': True})
finally:
    controller.close()
    Pin(8, Pin.IN)
