"""Buffered finite/infinite playback and producer fault tests; no jumper needed.

GP8 is output; GP9 is a PIO-observed trigger pad. Both return to inputs. Run with
tools/board.py after installing this checkout's compiled modules.
"""
from time import ticks_us, ticks_diff, sleep_ms
begin = ticks_us()
from ctrlaer import CtrlAer, pulse, chirp, OFF, HIGH, wait_for
from machine import Pin
from ctrlaer_runtime import UnderrunError, GateTimeoutError
import gc
import json
from uctypes import addressof

result = {}
controller = CtrlAer(0, 8, 1)
try:
    run = controller.prepare([(chirp(90000,120000,sweep_ms=2),100)], mode='buffered')
    scratch=addressof(run.compiler.edge_buffer)
    for block in run.buffers:
        first=addressof(block.words)
        assert scratch+8192<=first or first+run.buffer_words*4<=scratch
        for receive in run._receive:
            start=addressof(receive.words)
            assert start+1024<=first or first+run.buffer_words*4<=start
            assert start+1024<=scratch or scratch+8192<=start
            assert receive.storage is block.storage
    assert abs(addressof(run._receive[0].words)-addressof(run._receive[1].words))>=1024
    del receive
    del block  # Do not retain this pool while testing replacement below.
    del scratch,first,start  # Raw addresses can also retain conservative-GC blocks.
    result['scratch_outside_dma_rings']='passed'
    run.start()
    result['cold_prepare_start_us'] = ticks_diff(ticks_us(),begin)
    assert result['cold_prepare_start_us'] < 500000, str(result['cold_prepare_start_us'])
    run.wait(1000)
    result['long_chirp'] = run.status()
    assert run.state == 'done' and run.refill_count > 2

    def infinite():
        while True:
            yield pulse(108050),20
            yield OFF,20
    run = controller.prepare(infinite, mode='buffered')
    run.start()
    sleep_ms(250)
    run.poll()
    assert run.active, str(run.error)
    gc.collect()
    sleep_ms(1000)
    run.poll()
    assert run.active, str(run.error)
    result['infinite'] = run.status()
    run.abort()
    assert Pin(8).value() == 0
    run.rearm().start()
    sleep_ms(30)
    assert run.active, str(run.error)
    run.abort()

    Pin(9,Pin.OUT,value=0)
    def triggered():
        while True:
            wait_for(9, timeout_ms=1000)
            yield pulse(108050),1
            yield OFF,1
    run = controller.prepare(triggered,mode='buffered')
    run.start()
    sleep_ms(5)
    run.poll()
    assert run.state in ('armed','waiting')
    # On an 8 MB PSRAM heap, mem_alloc() itself blocks for ~158 ms. Inspect
    # memory only while the hardware is waiting on a LOW trigger input.
    gc.collect()
    before = gc.mem_alloc()
    for _ in range(12):
        sleep_ms(3)
        Pin(9).value(1)
        sleep_ms(3)
        Pin(9).value(0)
    run.poll()
    assert run.active, str(run.error)
    assert len(run.gate_history) >= 20, str(run.gate_history)
    sleep_ms(5)
    run.poll()
    assert run.state == 'waiting',run.state
    gc.collect()
    growth = gc.mem_alloc() - before
    result['triggered'] = run.status()
    result['triggered']['heap_growth_bytes'] = growth
    run.abort()

    def timeout():
        wait_for(9,timeout_ms=10)
        yield HIGH,1
    run = controller.prepare(timeout(),mode='buffered',final_mask=1)
    try:
        run.start().wait(1000)
        raise AssertionError('expected gate timeout')
    except GateTimeoutError:
        assert Pin(8).value() == 1
        result['timeout'] = 'passed'

    def too_slow():
        for i in range(20):
            if i >= 2:
                sleep_ms(100)  # Deliberate producer deadline miss after prefill.
            yield pulse(108050),20
    run = controller.prepare(too_slow(),mode='buffered',final_mask=0)
    try:
        run.start().wait(1000)
        raise AssertionError('expected producer underrun')
    except UnderrunError:
        assert Pin(8).value() == 0
        result['underrun'] = 'passed'

    # Foreground abort can coincide with a scheduled completion callback.
    def short():
        yield HIGH,1
        yield OFF,1
    run=controller.prepare(short,mode='buffered',buffer_words=128)
    for _ in range(100):
        run.start()
        sleep_ms(2)
        run.abort()
        run.abort()
        assert run.state in ('done','aborted'),run.state
        assert Pin(8).value()==0
        run.rearm()
    result['completion_abort_race']='passed'
finally:
    controller.close()
    Pin(8,Pin.IN)
    Pin(9,Pin.IN)
print(json.dumps(result))
