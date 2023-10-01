from machine import Pin
from rp2 import PIO, asm_pio, StateMachine

ON = const(1)
OFF = const(0)
FREQ = const(108_050)


def mux(progs):
    N = len(progs)
    default = OFF, 1000
    times = [0] * N
    val = 0
    while True:
        alive = 0
        for i, prog in enumerate(progs):
            if times[i] == 0:
                try:
                    cmd = next(prog)
                    alive += 1
                except StopIteration:
                    cmd = default
                val &= ~(1 << i)
                val |= cmd[0] << i
                times[i] = cmd[1]
        min_time = min(times)
        if not alive:
            break
        yield val, min_time
        for i in range(N):
            times[i] -= min_time

def ticks(time):
    return time * FREQ // 1000

def run(progs, sm_number, base_pin):
    n_pins = len(progs)
    prog = mux(progs)

    @asm_pio(out_init=(PIO.OUT_LOW,)*n_pins, out_shiftdir=PIO.SHIFT_RIGHT, fifo_join=PIO.JOIN_TX)
    def oscillator():
        pull() # pin states
        mov(x, osr)

        out(pins, n_pins)[4]
        out(pins, n_pins)

        pull() # duration in ticks (1 tick = 1/FREQ s)
        mov(y, osr)

        label("loop")
        mov(osr, x)[1]
        out(pins, n_pins)[4]
        out(pins, n_pins)
        jmp(y_dec, "loop")[1]

    sm = StateMachine(sm_number, oscillator, freq=FREQ*2*5, out_base=Pin(base_pin))
    sm.active(1)
    p = sm.put

    for state, length in prog:
        p(state)
        p(ticks(length))
    sm.active(0)