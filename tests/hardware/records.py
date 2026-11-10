"""Write an actual device run/capture record; retrieve it with tools/board.py."""
from ctrlaer import CtrlAer, square_wave, compile_program, Trigger, DigitalCapture
from ctrlaer_records import save_record
from machine import Pin
import json

controller = CtrlAer(0, 8, 1)
capture = None
try:
    def experiment(pulse_ms=1):
        yield square_wave(10000),pulse_ms
    program = compile_program(experiment,parameters={'pulse_ms':1},names=['atomiser'])
    run = controller.prepare(program)
    capture = DigitalCapture(4, 8, sample_hz=1000000, duration_ms=2,
                             trigger=Trigger(8))
    capture.start()
    run.start().wait(1000)
    trace = capture.wait()
    comparison = trace.compare(program, tolerance_ns=2000)
    assert comparison['passed'], comparison
    path = save_record('/ctrlaer-hardware-check.json', program, run, trace,
                       results={'comparison': comparison, 'sensor_example': 123},
                       metadata={'check': 'device-record-round-trip'})
    print(json.dumps({'record': path, 'parameters': program.parameters,
                      'firmware_recorded': True, 'capture_match': comparison['passed']}))
finally:
    if capture:
        capture.close()
    controller.close()
    Pin(8, Pin.IN)
