"""Run on either board with tools/board.py; no external connections needed.

Measure cold import/start latency for a 20 ms chirp, then warm edge production
into a reusable 1024-word array. GP8 is the output and is returned to input. The
throughput figure excludes packing/queueing, so is not a streaming guarantee.
"""
from time import ticks_us, ticks_diff

begin = ticks_us()
from ctrlaer import CtrlAer, sweep, square_wave

controller = CtrlAer(0, 8, 1)
run = controller.prepare([(sweep(90000, 120000, sweep_ms=2), 20)])
prepared = ticks_diff(ticks_us(), begin)
run.start()
started = ticks_diff(ticks_us(), begin)
run.wait(1000)
controller.close()

from machine import Pin, freq
from ctrlaer_native import CarrierEdges, words
import gc
import json
import sys

Pin(8, Pin.IN)
result = {'board': sys.implementation._machine, 'clock_hz': freq(),
          'cold_prepare_us': prepared, 'cold_prepare_start_us': started}
buffer = words(1024)
for name, command in (
        ('pulse', square_wave(108050)),
        ('periodic_chirp', sweep(90000, 120000, sweep_ms=2)),
        ('nonperiodic_chirp', sweep(90001, 120000, sweep_ms=2))):
    gc.collect()
    begin = ticks_us()
    cursor = CarrierEdges.create(command, freq())
    cursor.fill_into(buffer, len(buffer))
    first = ticks_diff(ticks_us(), begin)
    gc.collect()
    before = gc.mem_alloc()
    begin = ticks_us()
    for _ in range(128):
        cursor.fill_into(buffer, len(buffer))
    elapsed = ticks_diff(ticks_us(), begin)
    gc.collect()
    result[name] = {'first_1024_us': first, 'warm_131072_us': elapsed,
                    'warm_edges_per_second': 131072000000 / elapsed,
                    'retained_heap_growth_bytes': gc.mem_alloc() - before,
                    'cache_bytes': len(cursor._cache[0]) if cursor._cache else 0,
                    'lookup_bytes': len(cursor._lut[0]) if cursor._lut else 0}
print(json.dumps(result))
