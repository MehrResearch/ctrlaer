"""PIO trigger/gate/retrigger checks using GP9 as an internal signal source."""
from ctrlaer import CtrlAer, HIGH, LOW, square_wave, wait_for, DigitalCapture, Trigger, Group
from ctrlaer_runtime import GateTimeoutError
from machine import Pin
import json, gc

output = CtrlAer(0, 8, 1)
source = CtrlAer(1, 9, 1)
capture = None
results = {}
try:
    def p():
        wait_for(9,timeout_ms=50)
        yield square_wave(100000),.5
        wait_for(9,edge='falling',timeout_ms=50)
        yield square_wave(100000),.5
    run = output.prepare(p)
    signal = source.prepare([(LOW,5),(HIGH,1),(LOW,2)])
    capture = DigitalCapture(4, 8, 2, sample_hz=25000000, duration_ms=3,
                             trigger=Trigger(9))
    capture.start()
    Group([run, signal]).start().wait(500)
    trace = capture.wait()
    first_output = next(trace.edges(0))
    # Includes the compiler's explicitly reported control-packet spacing.
    assert first_output[1] == 1 and first_output[0] / trace.sample_hz < .000020, first_output
    assert len(run.gate_history) == 4, run.status()
    results['edge_response_ns'] = first_output[0] * 1e9 / trace.sample_hz
    results['boundary_gate_events'] = len(run.gate_history)
    capture.close()
    capture = None
    del trace, p, run, signal
    gc.collect()

    def p():
        for _ in range(2):
            wait_for(9,timeout_ms=50)
            yield square_wave(100000),.1
    run = output.prepare(p)
    signal = source.prepare([(LOW,3),(HIGH,.25),(LOW,3),(HIGH,.25),(LOW,1)])
    capture = DigitalCapture(4, 8, 1, sample_hz=1000000, duration_ms=20)
    capture.start()
    Group([run, signal]).start().wait(500)
    trace = capture.wait()
    rises = sum(1 for _, value in trace.edges(0) if value)
    assert rises == 20, rises
    assert len(run.gate_history) == 4, run.status()
    results['retriggered_pulses'] = rises
    capture.close()
    capture = None
    source.close()
    Pin(9, Pin.OUT, value=1)

    # A high level does not count as a new rising edge.
    run = output.prepare([(square_wave(100000),.1)], trigger=Trigger(9, timeout_ms=5))
    try:
        run.start().wait(100)
        raise AssertionError('rising-edge trigger accepted an already high input')
    except GateTimeoutError:
        assert Pin(8).value() == 0 and run.state == 'error'
    results['trigger_timeout'] = True

    run = output.prepare([(HIGH,1)], trigger=Trigger(9, level=1, timeout_ms=5))
    run.start().wait(100)
    assert run.state == 'done' and Pin(8).value() == 0
    results['ready_level'] = True
    print(json.dumps(results))
finally:
    if capture:
        capture.close()
    output.close()
    source.close()
    Pin(8, Pin.IN)
    Pin(9, Pin.IN)
