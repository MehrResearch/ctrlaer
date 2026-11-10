"""Measure repeated triggered HIGH/LOW sequences with a second PIO block.

Local loopback: Tiny 2040 GP2 -> GP6; Pico Plus 2 GP1 -> GP8. Output GP18
is sampled internally, so it needs no extra wire. Override CASES before running
to select (duration_ms, pairs_per_trigger, trigger_count) tuples.
"""
import gc
import json
import sys
from machine import Pin
from time import sleep_ms
from ctrlaer import CtrlAer, HIGH, OFF, wait_for
from ctrlaer_program import Trigger
from ctrlaer_capture import DigitalCapture

tiny = 'Tiny 2040' in sys.implementation._machine
source_pin, input_pin = (2, 6) if tiny else (1, 8)
source = Pin(source_pin, Pin.OUT, value=0)
Pin(input_pin, Pin.IN, Pin.PULL_DOWN)
controller = capture = None
results = []
try:
    for duration, pairs, shots in globals().get('CASES', (
            (800, 1, 3), (800, 5, 3), (80, 5, 10), (8, 5, 30),
            (.8, 5, 30), (.08, 5, 30), (.02, 5, 30), (.01, 5, 30))):
        gc.collect()

        def program():
            while True:
                wait_for(pin=input_pin)
                for _ in range(pairs):
                    yield HIGH, duration
                    yield OFF, duration

        controller = CtrlAer(sm_number=0, base_pin=18, n_pins=1, freq=100000)
        run = controller.prepare(program, mode='buffered')
        rate = max(2000, min(10000000, 64000 / duration))
        margin = max(.1, min(10, duration * .1))
        capture = DigitalCapture(sm_number=4, base_pin=18, sample_hz=rate,
            duration_ms=2 * pairs * duration + margin,
            trigger=Trigger(input_pin, edge='rising'), max_bytes=32768)
        run.start()
        max_width_error_us = 0
        latency_samples = []
        for shot in range(shots):
            if shot:
                capture.arm()
            capture.start()
            sleep_ms(3 + (shot * 17) % 19)
            assert Pin(input_pin).value() == 0, 'loopback LOW failed'
            source.value(1)
            sleep_ms(2)
            assert Pin(input_pin).value() == 1, 'loopback HIGH failed'
            source.value(0)
            trace = capture.wait(timeout_ms=int(2 * pairs * duration + 1000))
            sleep_ms(int(margin + 2))
            run.poll()
            assert run.error is None, str(run.error)
            edges = [(sample, level) for sample, level in trace.edges(0, True)
                     if sample or level]
            assert len(edges) == 2 * pairs, str((duration, pairs, shot, edges))
            assert [level for _, level in edges] == [1, 0] * pairs
            latency_samples.append(edges[0][0])
            for (first, _), (second, _) in zip(edges, edges[1:]):
                error = abs((second - first) * 1000000 / trace.sample_hz - duration * 1000)
                assert error <= 1100000 / trace.sample_hz, str((duration, error, trace.sample_hz))
                max_width_error_us = max(max_width_error_us, error)
        run.abort()
        assert Pin(18).value() == 0
        status = run.status()
        result = {'duration_ms': duration, 'pairs': pairs, 'triggers': shots,
                  'sample_hz': capture.sample_hz, 'max_width_error_us': max_width_error_us,
                  'first_rise_samples': [min(latency_samples), max(latency_samples)],
                  'refills': status['refill_count'], 'max_gc_us': status['max_gc_us'],
                  'max_refill_us': status['max_refill_us']}
        results.append(result)
        capture.close()
        controller.close()
        capture = controller = None
        del run, trace, edges
        print(json.dumps(result))
finally:
    if capture is not None:
        capture.close()
    if controller is not None:
        controller.close()
    source.value(0)
    if tiny:
        for pin in (18, 19, 20):
            Pin(pin, Pin.OUT, value=1)
    else:
        Pin('LED', Pin.OUT, value=0)
