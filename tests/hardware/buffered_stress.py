"""Run continuous and sparse buffered streams through many refills/GC cycles.

No external wiring. GP8 is the output and returns to input. Heap statistics are
read only after stopping: scanning an RP2350 PSRAM heap can block for 158 ms.
The seven workloads take about four minutes at the default 30 seconds each.
"""
from ctrlaer import CtrlAer, square_wave, sweep, OFF
from machine import Pin
from time import sleep_ms, ticks_ms, ticks_diff
import gc
import json

SOAK_SECONDS=30

controller=CtrlAer(0,8,1)
result={}

def continuous(command):
    while True:
        yield command,60000  # Cross native coordinate rollover within one ON.

def bursts():
    while True:
        yield square_wave(108050),20
        yield OFF,20

def chirp_bursts():
    while True:
        yield sweep(90000,120000,sweep_ms=2),20
        yield OFF,20

def nonperiodic_bursts():
    while True:
        yield sweep(90001,120000,sweep_ms=2),20
        yield OFF,20

try:
    for name,source in (
            ('continuous_pulse',lambda:continuous(square_wave(108050))),
            ('continuous_chirp',lambda:continuous(sweep(90000,120000,sweep_ms=2))),
            ('bursts',bursts),
            ('repeated_chirp',chirp_bursts),
            ('default_sweep',lambda:continuous(sweep(90000,120000))),
            ('nonperiodic_chirp',lambda:continuous(sweep(90001,120000,sweep_ms=2))),
            ('nonperiodic_bursts',nonperiodic_bursts)):
        run=controller.prepare(source,mode='buffered')
        run.start()
        started=ticks_ms()
        for _ in range(SOAK_SECONDS):
            sleep_ms(1000)
            run.poll()
            assert run.active,name+': '+json.dumps(run.status())
        run.abort()
        status=run.status()
        assert not status['error'],name+': '+str(status['error'])
        status['wall_ms']=ticks_diff(ticks_ms(),started)
        gc.collect()
        status['retained_heap_bytes']=gc.mem_alloc()
        result[name]=status
finally:
    controller.close()
    Pin(8,Pin.IN)
print(json.dumps(result))
