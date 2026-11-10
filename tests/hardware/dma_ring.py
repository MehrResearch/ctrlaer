"""Check DMA count reload and ring addressing on either RP2; no wiring needed."""
from array import array
from uctypes import addressof
from machine import mem32
from rp2 import DMA, StateMachine, PIO, asm_pio
from time import ticks_ms, ticks_diff, sleep_ms
import json


@asm_pio()
def echo():
    pull()
    mov(isr, osr)
    push()


def aligned(count):
    storage = array('I', bytearray(count * 8))
    offset = (-addressof(storage)) % (count * 4)
    return storage, memoryview(storage)[offset // 4:offset // 4 + count]


a_owner, a = aligned(64)
b_owner, b = aligned(64)
for i in range(64):
    a[i] = i + 1
    b[i] = i + 65
result = array('I', bytearray(1024 * 4))
sm = StateMachine(0, echo, freq=100000)
tx_a, tx_b, rx = DMA(), DMA(), DMA()
try:
    tx_a.config(read=a, write=sm, count=64,
                ctrl=tx_a.pack_ctrl(inc_write=False, ring_size=8, ring_sel=False,
                                    chain_to=tx_b.channel, treq_sel=0, irq_quiet=False))
    tx_b.config(read=b, write=sm, count=64,
                ctrl=tx_b.pack_ctrl(inc_write=False, ring_size=8, ring_sel=False,
                                    chain_to=tx_a.channel, treq_sel=0, irq_quiet=False))
    rx.config(read=sm, write=result, count=len(result),
              ctrl=rx.pack_ctrl(inc_read=False, treq_sel=4), trigger=True)
    tx_a.active(1)
    sm.active(1)
    started = ticks_ms()
    while rx.active() and ticks_diff(ticks_ms(), started) < 1000:
        sleep_ms(1)
    assert not rx.active(), 'RX never collected the cyclic stream'
    assert list(result) == [i % 128 + 1 for i in range(1024)], 'Count reload/ring data mismatch'
    assert mem32[0x50000400] & (1 << tx_a.channel | 1 << tx_b.channel)
    print(json.dumps({'passed':True,'words':len(result),'cycles':8,
                      'a_address_aligned':addressof(a)%256==0,
                      'b_address_aligned':addressof(b)%256==0}))
finally:
    sm.active(0)
    for dma in (tx_a, tx_b, rx):
        dma.active(0)
        dma.close()
    PIO(0).remove_program(echo)
