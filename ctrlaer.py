"""CtrlAer signal primitives and the backwards-compatible streaming controller.

The signal primitives are also importable on CPython for offline validation.
"""
from math import isfinite
from time import sleep as _time_sleep
from builtins import print as _builtin_print
import sys

try:
    from micropython import const
except ImportError:
    def const(value):
        return value

try:
    from machine import Pin, freq, mem32
    from rp2 import PIO, asm_pio, StateMachine
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


def _clock_divider(target, clock):
    _duration(target)
    if target == 0 or not 1 <= clock / target <= 65536:
        raise ValueError("carrier frequency is outside the state machine clock range")
    fixed = int(clock / target * 256)
    return ((fixed >> 8) & 0xffff, fixed & 255)


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
    states, durations, offsets = [0] * N, [0] * N, [0] * N
    val = 0
    while True:
        for i, prog in enumerate(progs):
            while not done[i] and times[i] == 0:
                try:
                    state, duration = next(prog)
                except StopIteration:
                    done[i] = True
                    state, duration = OFF, 0
                if not isinstance(state, _Carrier) and (not isinstance(state, int) or not 0 <= state <= 3):
                    raise ValueError("channel state must be a CtrlAer signal command")
                duration = _duration(duration)
                states[i], durations[i], offsets[i] = state, duration, 0
                val &= ~((1 << i) + (1 << (N + i)))
                if isinstance(state, int):
                    val |= (state & 1) << i
                    val |= (state >> 1) << (N + i)
                times[i] = duration
        active_times = [times[i] for i in range(N) if not done[i]]
        if not active_times:
            return
        min_time = min(active_times)
        if any(isinstance(state, _Carrier) for state in states):
            yield _Parallel(tuple(_Signal(states[i], durations[i], offsets[i]) for i in range(N))), min_time
        else:
            yield val, min_time
        for i in range(N):
            if not done[i]:
                times[i] -= min_time
                offsets[i] += min_time


class CtrlAer:
    def __init__(self, sm_number, base_pin, n_pins, freq=FREQ):
        if Pin is None:
            raise RuntimeError("CtrlAer execution requires MicroPython on an RP2040/RP2350")
        if not isinstance(n_pins, int) or not 1 <= n_pins <= 16:
            raise ValueError("n_pins must be between 1 and 16")
        sm_count = 12 if "RP2350" in sys.implementation._machine else 8
        if not isinstance(sm_number, int) or not 0 <= sm_number < sm_count:
            raise ValueError("invalid state machine number for this board")
        if not isinstance(base_pin, int) or not 0 <= base_pin <= 30 - n_pins:
            raise ValueError("output pins must fit within GPIO 0 to 29")
        self.n_pins = n_pins
        self._freq = freq
        if _duration(freq) == 0:
            raise ValueError("frequency must be positive")
        self.base_pin, self._sm_id = base_pin, sm_number
        self.pio_index = sm_number // 4
        self.sm_number = sm_number % 4
        self.bit_mask = 1 << self.sm_number
        self._sm = None
        self._prepared = None
        self._legacy_program = None

    @property
    def sm(self):
        if self._sm is None:
            self._ensure_legacy()
        return self._sm

    @sm.setter
    def sm(self, value):
        self._sm = value

    def _ensure_legacy(self):
        if self._prepared is not None and not self._prepared.closed:
            raise RuntimeError("close the prepared run before using streaming execution")
        from ctrlaer_runtime import claim, release
        _clock_divider(self._freq * 14, freq())
        claim(self, self._sm_id, self.base_pin, self.n_pins)
        n_pins = self.n_pins

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

        try:
            self._sm = StateMachine(self._sm_id, oscillator,
                                    freq=int(self._freq * 14), out_base=Pin(self.base_pin))
            self._legacy_program = oscillator
            self._start_pc = mem32[0x502000d4 + self.pio_index * 0x100000 + self.sm_number * 24]
            self._sm.active(1)
        except Exception:
            release(self)
            raise

    def set_freq(self, freq):
        if self._prepared is not None and not self._prepared.closed:
            raise RuntimeError("prepared frequencies belong to the compiled program; prepare a new program")
        if _duration(freq) == 0:
            raise ValueError("frequency must be positive")
        address = 0x502000c8 + (self.pio_index * 0x100000) + self.sm_number * 24
        if self._sm is not None:
            i, f = _clock_divider(freq * 14, globals()["freq"]())
            mem32[address] = (i << 16) | (f << 8)
        self._freq = freq
        
    def ticks(self, time):
        return int(_duration(time) * self._freq / 1000)
    
    def is_full(self):
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 16))
    
    def is_empty(self):
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 24))
    
    def block(self, timeout_ms=None):
        if self._prepared is not None and not self._prepared.closed:
            return self._prepared.wait(timeout_ms)
        if self._sm is None:
            return
        if not self._sm.active():
            if self.is_empty():
                return
            raise RuntimeError("cannot wait for queued data on a disabled state machine")
        from time import ticks_ms, ticks_diff
        if timeout_ms is not None and _duration(timeout_ms) == 0:
            raise ValueError("timeout must be positive")
        start = ticks_ms()
        # TXEMPTY becomes true as soon as the duration is loaded. Completion is
        # the next blocked state-word PULL, after the final duration has elapsed.
        debug = 0x50200008 + self.pio_index * 0x100000
        address = 0x502000d4 + self.pio_index * 0x100000 + self.sm_number * 24
        mem32[debug] = self.bit_mask << 24
        while not (self.is_empty() and mem32[debug] & (self.bit_mask << 24)
                   and mem32[address] == self._start_pc):
            if timeout_ms is not None and ticks_diff(ticks_ms(), start) >= timeout_ms:
                raise RuntimeError("streaming completion wait timed out")
            _time_sleep(0.001)

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
                state, length = _advance(prog, self._stream_effect)
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
                _builtin_print(f'{cmd},{duration}')
        self.run(fn())

    def prepare(self, program, **kwargs):
        """Compile and arm a finite or explicitly bounded generator program."""
        from ctrlaer_runtime import PreparedRun
        if self._prepared is not None and not self._prepared.closed:
            if self._prepared.active:
                raise RuntimeError("abort or wait for the current run before preparing another")
            self._prepared.close()
        if self._sm is not None:
            self.close()
        self._prepared = PreparedRun(self, program, **kwargs)
        return self._prepared

    def start(self):
        if self._prepared is None:
            raise RuntimeError("prepare a program before starting")
        return self._prepared.start()

    def wait(self, timeout_ms=None):
        if self._prepared is None:
            return self.block(timeout_ms)
        return self._prepared.wait(timeout_ms)

    def abort(self):
        if self._prepared is not None:
            return self._prepared.abort()
        if self._sm is not None:
            self._sm.active(0)

    def close(self):
        if self._prepared is not None and not self._prepared.closed:
            self._prepared.close()
        if self._sm is not None:
            from ctrlaer_runtime import release
            self._sm.active(0)
            PIO(self.pio_index).remove_program(self._legacy_program)
            release(self)
            self._sm = self._legacy_program = None

    def _stream_effect(self, kind, value):
        if kind == "wait":
            raise RuntimeError("hardware wait_for commands require prepare(); the generator API is unchanged")
        self.block()
        if kind == "sleep":
            _time_sleep(value)
        elif kind == "print":
            _builtin_print(value, end="")


from ctrlaer_commands import pulse, chirp, sleep, print, wait_for, _Carrier, _Signal, _Parallel, _advance
from ctrlaer_program import Trigger, compile_program
from ctrlaer_capture import Trace, DigitalCapture


class Group:
    """Coordinate prepared runs; hardware imports remain deferred on CPython."""
    def __new__(cls, runs):
        from ctrlaer_runtime import PreparedGroup
        return PreparedGroup(runs)
