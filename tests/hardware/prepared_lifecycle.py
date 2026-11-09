"""Prepared runtime checks for an otherwise idle Tiny 2040 (unconnected GP8)."""
from ctrlaer import CtrlAer, HIGH, LOW, pulse
from ctrlaer_runtime import UnderrunError, ExecutionTimeoutError
from machine import Pin
from time import ticks_us, ticks_diff, sleep_ms
import json

results = {}
controller = CtrlAer(0, 8, 1, freq=100000)
try:
    run = controller.prepare([(HIGH,20),(LOW,1)])
    assert run.state == 'prepared' and not run.sm.active()
    start = ticks_us()
    run.start().wait(500)
    elapsed = ticks_diff(ticks_us(), start)
    assert run.state == 'done' and elapsed >= 21000, (run.status(), elapsed)
    assert Pin(8).value() == 0
    results['completion_us'] = elapsed
    run.rearm().start().wait(500)
    assert run.state == 'done'
    results['rearm'] = True

    run = controller.prepare([(HIGH,500)], final_mask=1)
    run.start()
    sleep_ms(2)
    run.abort()
    assert run.state == 'aborted' and Pin(8).value() == 1
    results['abort_final_high'] = True

    run = controller.prepare([(HIGH,500)])
    try:
        run.start().wait(2)
        raise AssertionError('wait timeout did not fire')
    except ExecutionTimeoutError:
        assert Pin(8).value() == 0 and run.state == 'error'
    results['wait_timeout'] = True

    run = controller.prepare([(pulse(100000),2)], final_mask=0)
    run._tx.active(0)  # Inject an actual missing DMA feeder, after FIFO prefill.
    try:
        run.start().wait(500)
        raise AssertionError('underrun did not fire')
    except UnderrunError:
        assert run.status()['underrun'] and Pin(8).value() == 0
    results['injected_underrun'] = True
    print(json.dumps(results))
finally:
    controller.close()
    Pin(8, Pin.IN)
