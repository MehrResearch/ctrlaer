"""GPIO readback of exact prepared edges, independent carriers and dead time."""
from ctrlaer import CtrlAer, square_wave, mux, compile_program, DigitalCapture, Trigger, Group
from machine import Pin
import json, gc

results = {}
controller = CtrlAer(0, 8, 2)
capture = other = None
try:
    program = compile_program(mux([[(square_wave(100000,duty=.25),1)],
                                    [(square_wave(40000,duty=.6),1)]]),n_pins=2)
    run = controller.prepare(program)
    capture = DigitalCapture(4, 8, 2, sample_hz=25000000, duration_ms=1.2,
                             trigger=Trigger(8))
    capture.start()
    run.start().wait(500)
    trace = capture.wait()
    comparison = trace.compare(program, tolerance_ns=80)
    assert comparison['passed'], comparison
    results['duty_and_multirate'] = comparison
    capture.close()
    capture = None
    del trace, program
    gc.collect()

    program = compile_program(mux([[(square_wave(100000,dead_time_ns=200),1)],
        [(square_wave(100000,complement=True,dead_time_ns=200),1)]]),n_pins=2)
    run = controller.prepare(program)
    capture = DigitalCapture(4, 8, 2, sample_hz=25000000, duration_ms=1.2,
                             trigger=Trigger(8))
    capture.start()
    run.start().wait(500)
    trace = capture.wait()
    comparison = trace.compare(program, tolerance_ns=80)
    assert comparison['passed'], comparison
    assert not any(level == 3 for level in trace.levels())
    results['dead_time_no_overlap'] = True
    capture.close()
    capture = None
    controller.close()
    del trace, program, run
    gc.collect()

    controller = CtrlAer(0, 8, 1)
    other = CtrlAer(1, 9, 1)
    a = controller.prepare([(square_wave(108050),1)])
    b = other.prepare([(square_wave(111500),1)])
    capture = DigitalCapture(4, 8, 2, sample_hz=25000000, duration_ms=1.2,
                             trigger=Trigger(8))
    capture.start()
    Group([a, b]).start().wait(500)
    trace = capture.wait()
    ca = trace.compare(a.program, channels={0: 0}, tolerance_ns=80)
    cb = trace.compare(b.program, channels={0: 1}, tolerance_ns=80)
    assert ca['passed'] and cb['passed'], (ca, cb)
    first = next(trace.levels())
    assert first == 3, first  # Both first edges share the hardware start.
    results['independent_group_carriers'] = [ca, cb]
    print(json.dumps(results))
finally:
    if capture:
        capture.close()
    controller.close()
    if other:
        other.close()
    Pin(8, Pin.IN)
    Pin(9, Pin.IN)
