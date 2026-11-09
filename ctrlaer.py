"""CtrlAer signal primitives and the backwards-compatible streaming controller.

The signal primitives are also importable on CPython for offline validation.
"""
from math import isfinite
from time import sleep

try:
    from micropython import const
except ImportError:
    def const(value):
        return value

try:
    from machine import Pin, freq, mem32
    from rp2 import PIO, asm_pio, StateMachine
    from rp2040hw.pio import pios, clkdiv
except ImportError:
    Pin = None

PULSE10 = const(0b10)
PULSE01 = const(0b01)
PULSE11 = const(0b11)
PULSE00 = const(0b00)
HIGH = PULSE11
LOW = PULSE00
ON = PULSE10
OFF = PULSE00
FREQ = const(108_050)
RP_CLK = freq() if Pin is not None else None


def _duration(value):
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("duration must be a finite, non-negative number")
    return value


def _always_on():
    while True:
        yield ON, 1000


def _inactive():
    # Yielding once is enough: mux() yields OFF when the generator is exhausted.
    # This guarantees that mux() terminates after the last active program.
    yield OFF, 1000


inactive = _inactive()
always_on = _always_on()


def mux(progs):
    """Merge channel generators, keeping unfinished channels alive to their end.

    Zero-length segments are skipped. Exhausted channels become LOW while other
    channels continue. Each two-bit state occupies two *bit planes* in the result.
    """
    N = len(progs)
    if not 1 <= N <= 16:
        raise ValueError("mux needs 1 to 16 channels")
    progs = [iter(prog) for prog in progs]
    times = [0] * N
    done = [False] * N
    val = 0
    while True:
        for i, prog in enumerate(progs):
            while not done[i] and times[i] == 0:
                try:
                    state, duration = next(prog)
                except StopIteration:
                    done[i] = True
                    state, duration = OFF, 0
                if not isinstance(state, int) or not 0 <= state <= 3:
                    raise ValueError("channel state must be an integer from 0 to 3")
                duration = _duration(duration)
                val &= ~((1 << i) + (1 << (N + i)))
                val |= (state & 1) << i
                val |= (state >> 1) << (N + i)
                times[i] = duration
        active_times = [times[i] for i in range(N) if not done[i]]
        if not active_times:
            return
        min_time = min(active_times)
        yield val, min_time
        for i in range(N):
            if not done[i]:
                times[i] -= min_time


class CtrlAer:
    def __init__(self, sm_number, base_pin, n_pins, freq=FREQ):
        if Pin is None:
            raise RuntimeError("CtrlAer execution requires MicroPython on an RP2040/RP2350")
        if not isinstance(n_pins, int) or not 1 <= n_pins <= 16:
            raise ValueError("n_pins must be between 1 and 16")
        if not isinstance(sm_number, int) or not 0 <= sm_number < len(pios) * 4:
            raise ValueError("invalid state machine number for this board")
        if not isinstance(base_pin, int) or not 0 <= base_pin <= 30 - n_pins:
            raise ValueError("output pins must fit within GPIO 0 to 29")
        self.n_pins = n_pins
        self._freq = freq
        self.pio_index = sm_number // 4
        self.pio = pios[sm_number // 4]
        self.sm_number = sm_number % 4
        self.bit_mask = 1 << self.sm_number
        self.sm_reg = self.pio.SM[self.sm_number]

        @asm_pio(
            out_init=(PIO.OUT_LOW,) * n_pins,
            out_shiftdir=PIO.SHIFT_RIGHT,
            fifo_join=PIO.JOIN_TX,
        )
        def oscillator():
            label("start")
            pull()  # pin states
            mov(x, osr)

            out(pins, n_pins)[6]
            out(pins, n_pins)

            pull()  # duration in ticks (1 tick = 1/freq s)
            mov(y, osr)
            jmp(y_dec, "loop")
            jmp("start")

            label("loop")
            mov(osr, x)[2]
            out(pins, n_pins)[6]
            out(pins, n_pins)[2]
            jmp(y_dec, "loop")
            nop()

        self.sm = StateMachine(
            sm_number, oscillator, freq=self._freq * 2 * 7, out_base=Pin(base_pin)
        )
        self._start_pc = mem32[0x502000d4 + (sm_number // 4) * 0x100000 + self.sm_number * 24]
        self.sm.active(1)

    def set_freq(self, freq):
        i, f = clkdiv(freq * 2 * 7, globals()["freq"]())
        address = 0x502000c8 + (self.pio_index * 0x100000) + self.sm_number * 24
        mem32[address] = (i << 16) | (f << 8)
        self._freq = freq
        
    def ticks(self, time):
        return int(_duration(time) * self._freq / 1000)
    
    def is_full(self):
        return (self.pio.FSTAT.TXFULL & self.bit_mask) >> self.sm_number
    
    def is_empty(self):
        return (self.pio.FSTAT.TXEMPTY & self.bit_mask) >> self.sm_number
    
    def block(self):
        # TXEMPTY becomes true as soon as the duration is loaded. Completion is
        # the next blocked state-word PULL, after the final duration has elapsed.
        debug = 0x50200008 + self.pio_index * 0x100000
        address = 0x502000d4 + self.pio_index * 0x100000 + self.sm_number * 24
        mem32[debug] = self.bit_mask << 24
        while not (self.is_empty() and mem32[debug] & (self.bit_mask << 24)
                   and mem32[address] == self._start_pc):
            sleep(0.001)

    def run(self, prog, block=True, use_ms=True):
        """Execute a program on the CtrlAer.
        
        Args:
            prog: An iterable yielding (state, duration) tuples
            block: If True, blocks until FIFO has space; if False, returns when FIFO is full
            use_ms: If True, interpret durations as milliseconds; if False, interpret as raw PIO ticks
        """
        put = self.sm.put
        unit = self.ticks if use_ms else lambda x: x
        prog = iter(prog)
        while True:
            # Check room for a whole pair before advancing a generator. Returning
            # the same iterator lets the caller resume without losing a command.
            if not block and self.sm.tx_fifo() > 6:
                return prog
            try:
                state, length = next(prog)
            except StopIteration:
                return None
            if not isinstance(state, int) or not 0 <= state < (1 << (2 * self.n_pins)):
                raise ValueError("state does not fit the configured channels")
            length = _duration(length)
            if length == 0:
                continue
            ticks = unit(length)
            if not isinstance(ticks, int) or not 1 <= ticks <= 0x100000000:
                raise ValueError("duration must encode between 1 and 2**32 ticks")
            put(state)
            put(ticks - 1)
    
    def listen(self, io):
        def fn():
            while True:
                line = io.readline()
                if line in (b'', ''):
                    return
                line = line.strip()
                if line in (b'END', 'END'):
                    break
                if not line:
                    continue
                cmd, duration = line.split(b',' if isinstance(line, bytes) else ',')
                cmd = int(cmd)
                duration = int(duration)
                yield cmd, duration
                print(f'{cmd},{duration}')
        self.run(fn())
