from machine import Pin
from rp2 import PIO, asm_pio, StateMachine

ON = const(1)
OFF = const(0)
FREQ = const(108_050)

@asm_pio(sideset_init=(PIO.OUT_LOW,))
def oscillator():
    label("top")
    irq(rel(0))
    # First value: 0: delay, 1: square wave
    pull()
    mov(x, osr)
    # Second value: duration in ticks (1 tick = 1/FREQ s)
    pull()
    mov(y, osr)

    jmp(not_x, "delay")
    label("loop")
    nop().side(1)
    jmp(y_dec, "loop").side(0)
    jmp("top")
    
    label("delay")
    nop()
    jmp(y_dec, "delay")

class Piezo:
    def __init__(self, sm, pin):
        self.sm = StateMachine(sm, oscillator, freq=FREQ*2, sideset_base=Pin(pin))
        self.sm.irq(self._irq)
    
    def run(self, prog):
        self.sm.active(1)
        self.prog = prog()
    
    def _irq(self, sm):
        try:
            state, length = next(self.prog)
            self.sm.put(state)
            self.sm.put(length)
        except StopIteration:
            self.sm.active(0)

def ticks(time):
    return round(time * FREQ)

