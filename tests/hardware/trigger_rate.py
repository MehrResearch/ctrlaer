"""Count every output from regularly spaced hardware triggers over local jumpers.

Tiny GP2 -> GP6; Pico Plus 2 GP1 -> GP8. PIO1 generates the triggers and counts
GP18 rising edges independently of Python. Each trigger requests five pairs of
10 us HIGH / 10 us LOW. Override RATES or SHOTS before running if desired.
"""
import json
import sys
from machine import Pin, mem32
from rp2 import PIO, StateMachine, asm_pio
from time import sleep_ms, ticks_ms, ticks_diff
from ctrlaer import CtrlAer, HIGH, OFF, wait_for


@asm_pio(set_init=PIO.OUT_LOW)
def trigger_clock():
    wrap_target()
    label('shot')
    set(pins, 1)[31]
    set(pins, 0)[31]
    mov(y, osr)
    label('delay')
    jmp(y_dec, 'delay')
    jmp(x_dec, 'shot')
    irq(block, rel(0))
    wrap()


@asm_pio()
def count_rises():
    wrap_target()
    label('edge')
    wait(0, pin, 0)
    wait(1, pin, 0)
    jmp(x_dec, 'edge')
    wrap()


tiny = 'Tiny 2040' in sys.implementation._machine
source_pin, input_pin = (2, 6) if tiny else (1, 8)
Pin(input_pin, Pin.IN, Pin.PULL_DOWN)
shots = globals().get('SHOTS', 200)
controller = source = counter = None
try:
    for rate in globals().get('RATES', (10, 50) if tiny else (10, 50, 100)):
        def program():
            while True:
                wait_for(input_pin)
                for _ in range(5):
                    yield HIGH, .01
                    yield OFF, .01
        controller = CtrlAer(sm_number=0, base_pin=18, n_pins=1, freq=100000)
        run = controller.prepare(program, mode='buffered')
        source = StateMachine(4, trigger_clock, freq=1000000, set_base=Pin(source_pin))
        source.put(shots - 1)
        source.exec('pull()')
        source.exec('mov(x, osr)')
        source.put(round(1000000/rate) - 67)
        source.exec('pull()')
        mem32[0x50300030] = 1
        counter = StateMachine(5, count_rises, in_base=Pin(18))
        counter.exec('set(x, 0)')
        run.start()
        counter.active(1)
        source.active(1)
        started = ticks_ms()
        while not mem32[0x50300030] & 1:
            assert ticks_diff(ticks_ms(), started) < shots * 1000 / rate + 2000
            sleep_ms(1)
        sleep_ms(10)
        source.active(0)
        counter.active(0)
        run.abort()
        assert run.error is None, str(run.error)
        controller.close()  # Free the waveform pool before decoding the count.
        counter.exec('mov(isr, x)')
        counter.exec('push()')
        observed = (-counter.get()) & 0xffffffff
        assert observed == shots * 5, str((rate, observed, shots * 5))
        print(json.dumps({'trigger_hz': rate, 'triggers': shots, 'output_rises': observed,
                          'duration_us': 10, 'max_refill_us': run.max_refill_us,
                          'max_gc_us': run.max_gc_us}))
        PIO(1).remove_program(trigger_clock)
        PIO(1).remove_program(count_rises)
        controller = source = counter = None
        del run
finally:
    if source is not None:
        source.active(0)
    if counter is not None:
        counter.active(0)
    if controller is not None:
        controller.close()
    Pin(source_pin, Pin.OUT, value=0)
    if tiny:
        for pin in (18, 19, 20):
            Pin(pin, Pin.OUT, value=1)
    else:
        Pin('LED', Pin.OUT, value=0)
