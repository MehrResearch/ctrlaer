"""Exercise common generator/parameter shapes for sustained buffered playback.

GP8 through GP12 are internal/unconnected outputs, restored to inputs. No
external wiring; allow 60 seconds for all six workloads. Statistics are gathered
after abort, so heap scanning cannot stall an active waveform.
"""
from ctrlaer import CtrlAer, ON, OFF, PULSE01, chirp, mux
from machine import Pin
from time import sleep_ms, ticks_ms, ticks_diff
import gc
import json


def continuous(command):
    while True:
        yield command, 60000


def bursts(command):
    while True:
        yield command, 20
        yield OFF, 20


def phase_a():
    while True:
        yield ON, 20
        yield OFF, 20


def phase_b():
    while True:
        yield PULSE01, 10
        yield OFF, 30


results = {}
controller = None
try:
    for name, count, source in (
            ('continuous_integer', 1, lambda: continuous(ON)),
            ('integer_mux_2', 2, lambda: mux([phase_a(), phase_b()])),
            ('integer_mux_5', 5, lambda: mux([phase_a(), phase_b(), phase_a(),
                                            phase_b(), phase_a()])),
            ('repeated_chirp', 1, lambda: bursts(chirp(90000, 120000, sweep_ms=2))),
            ('descending_chirp', 1, lambda: continuous(
                chirp(120000, 90000, sweep_ms=2, duty=.4, phase=.25))),
            ('default_20ms_sweep', 1, lambda: continuous(chirp(90000, 120000)))):
        controller = CtrlAer(0, 8, count)
        run = controller.prepare(source, mode='buffered')
        run.start()
        started = ticks_ms()
        sleep_ms(5000)
        run.poll()
        assert run.active, name + ': ' + str(run.error)
        run.abort()
        status = run.status()
        assert not status['error'],name+': '+str(status['error'])
        status['wall_ms'] = ticks_diff(ticks_ms(), started)
        gc.collect()
        status['retained_heap_bytes'] = gc.mem_alloc()
        results[name] = status
        controller.close()
finally:
    if controller is not None:
        controller.close()
    for number in range(8, 13):
        Pin(number, Pin.IN)
print(json.dumps(results))
