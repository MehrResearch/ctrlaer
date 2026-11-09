"""Full 20 ms adiabatic pulse, captured on-chip at 8 MS/s on an idle Tiny 2040."""
from ctrlaer import CtrlAer, chirp, compile_program, DigitalCapture, Trigger
from machine import Pin
from time import ticks_ms, ticks_diff
import json, gc

controller = CtrlAer(0, 8, 1)
capture = None
try:
    def experiment():
        yield chirp(90000,120000,sweep_ms=2),20
    threshold = gc.threshold()
    started = ticks_ms()
    program = compile_program(experiment)
    compile_ms = ticks_diff(ticks_ms(), started)
    assert gc.threshold() == threshold
    run = controller.prepare(program)
    gc.collect()
    capture = DigitalCapture(4, 8, sample_hz=8000000, duration_ms=20.5,
                             trigger=Trigger(8))
    capture.start()
    run.start().wait(1000)
    trace = capture.wait(1000)
    comparison = trace.compare(program, tolerance_ns=250)
    assert comparison['passed'], comparison
    rises = [sample for sample, level in trace.edges(0, True) if level]
    assert len(rises) == 2100, len(rises)
    low_hz = 10 * trace.sample_hz / (rises[430] - rises[420])
    high_hz = 10 * trace.sample_hz / (rises[215] - rises[205])
    assert 89000 < low_hz < 93000, low_hz
    assert 118000 < high_hz < 122000, high_hz
    turns=[rises[210*i] for i in range(1,10)]
    assert all(abs(sample-16000*(i+1))<=2 for i,sample in enumerate(turns)),turns
    print(json.dumps({'pulse_count': len(rises), 'near_low_hz': low_hz,
                      'near_high_hz': high_hz, 'turnaround_samples':turns,
                      'sweep_ms':2, 'triangle_period_ms':4, 'nominal_duration_ms': program.duration_ms,
                      'program_bytes': len(program.words) * 4, 'comparison': comparison,
                      'compile_ms': compile_ms, 'gc_policy_restored': True,
                      'free_heap_after_capture': gc.mem_free()}))
finally:
    if capture:
        capture.close()
    controller.close()
    Pin(8, Pin.IN)
