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
    """Shared unbounded generator instance yielding `(ON, 1000)` repeatedly.

    Exported as `always_on`, with no call parentheses. Durations follow the
    consuming execution mode's units: normally milliseconds, or carrier periods
    with `use_ms=False`. Consumption advances this shared instance; define a
    fresh generator factory when independent state is needed. Finite analysis
    requires an explicit window.
    """
    while True:
        yield ON, 1000


def _inactive():
    """Shared generator instance yielding `(OFF, 1000)` once, exported as `inactive`.

    Durations follow the consuming mode's units. After consumption the instance
    remains exhausted; `mux()` keeps an exhausted channel LOW until its other
    channels finish. Define a factory for a fresh independently consumed source.
    """
    # Yielding once is enough: mux() yields OFF when the generator is exhausted.
    # This guarantees that mux() terminates after the last active program.
    yield OFF, 1000


inactive = _inactive()
always_on = _always_on()


def mux(progs):
    """Merge channel generators, keeping unfinished channels alive to their end.

    Zero-length segments are skipped. Exhausted channels become LOW while other
    channels continue. Each two-bit state occupies two *bit planes* in the result.

    `progs` is a sequence of 1–16 iterables, each yielding `(command, duration)`
    for one channel. Integer channel states are 0–3; rich `pulse`/`chirp` commands
    are accepted in DMA/analysis modes. Channel order maps to consecutive GPIOs
    from the controller's `base_pin`. Durations must be finite/non-negative.

    Returns a generator. The shortest unfinished segment sets each merged
    section boundary; other rich channels retain elapsed phase/sweep position.
    Managed sleep/print/wait effects are barriers for the whole shared engine.
    All merged edge intervals must fit its six-clock minimum. Use independent
    state machines in a `Group` when carriers have arbitrarily close crossings
    or need independent waits. Invalid channel counts/commands raise `ValueError`.
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
    """Own a contiguous group of output GPIOs on one PIO state machine.

    - `sm_number`: global state-machine index, 0–7 on RP2040 or 0–11 on RP2350.
      Four consecutive indices belong to each PIO block.
    - `base_pin`: GPIO number of channel 0. Channel `i` uses `base_pin + i`.
      Outputs must fit within GPIO0–GPIO29 and the board's exposed pins.
    - `n_pins`: number of contiguous channels, 1–16.
    - `freq`: positive carrier frequency in Hz for integer commands and the
      original FIFO engine. Rich `pulse`/`chirp` commands set their own frequency.

    Construction requires MicroPython on RP2. Hardware is claimed when first
    used, so a new controller does not start output by itself. Use `prepare`
    followed by `start`/`wait`, or choose an explicit mode with `run`. Always
    close the controller in a `finally` block to release its PIO/DMA resources.
    """
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
        """Original FIFO engine's low-level MicroPython `StateMachine`.

        Access allocates and enables that engine if necessary. It is unavailable
        while a prepared run is open; that run exposes its own `sm` attribute.
        Most applications should use the controller/run methods instead.
        """
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
        """Set the original FIFO carrier frequency in Hz; return `None`.

        An allocated FIFO engine changes divider immediately, including for
        queued commands. The divider is quantised to the hardware's fractional
        clock. An open prepared run rejects changes: prepare another waveform
        with new command parameters instead. Invalid frequencies raise
        `ValueError`; changing an open prepared controller raises `RuntimeError`.
        """
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
        """Convert non-negative milliseconds to whole carrier periods, rounding down.

        Uses the controller's configured `freq`, not the system clock or a rich
        command's frequency. Returns an integer; invalid durations raise
        `ValueError`.
        """
        return int(_duration(time) * self._freq / 1000)
    
    def is_full(self):
        """Return whether this state machine's TX FIFO is full; do not wait.

        This low-level FIFO status does not describe completion of an output
        interval or availability of the larger buffered-runtime queues.
        """
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 16))
    
    def is_empty(self):
        """Return whether this state machine's TX FIFO is empty; do not wait.

        An empty FIFO can coexist with an interval still executing in PIO.
        Use `block()`/`wait()` for completion.
        """
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 24))
    
    def block(self, timeout_ms=None):
        """Wait for queued FIFO output to finish, or delegate to a prepared run.

        `timeout_ms` is a positive timeout in milliseconds, or `None` for no
        deadline. Original FIFO completion means the engine has finished its
        last duration and blocked on the next state-word PULL; TXEMPTY alone is
        insufficient. Returns `None` for FIFO playback, or the prepared run.

        A FIFO timeout raises `RuntimeError` without aborting that engine.
        Prepared playback uses `PreparedRun.wait` error/final-mask semantics.
        """
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

    def run(self, prog, block=True, use_ms=True, mode=None, **options):
        """Execute the existing `(command, duration)` generator vocabulary.

        - `prog`: iterable of pairs; explicit DMA modes also accept a generator
          factory and its `parameters` preparation option.
        - `mode=None`: original FIFO engine, accepting integer bit-plane states.
          `block=True` waits for FIFO space while consuming the generator; it
          returns `None` at EOF before the last output necessarily finishes.
          Call `block()` afterwards to wait for completion. With `block=False`,
          return the same iterator when a whole pair will not fit, or `None` at
          EOF. Retain/resubmit that iterator to continue without losing commands.
        - `mode='buffered'`: bounded-memory DMA lookahead, including rich commands
          and hardware gates. `block=True` waits for completion and returns the
          run. `block=False` starts playback and returns its active run.
        - `mode='eager'`: finish each command before resuming Python, holding the
          last digital level in interpreter/preparation gaps. Synchronous only;
          `block=False` is rejected. Returns the completed run at finite EOF.
        - `use_ms`: durations are milliseconds when true, or periods of the
          controller's `freq` when false. These are not system-clock ticks.
        - `**options`: forwarded preparation options in explicit DMA modes,
          including `final_mask`, `trigger`, `buffer_words` and `parameters`.

        Eager/buffered errors apply `final_mask` and propagate. The original
        engine supports managed sleep/print, but hardware `wait_for` and rich
        commands require a DMA mode. Close the controller in `finally`.
        """
        if mode == "eager":
            if not block:
                raise ValueError("eager execution is synchronous; block must be True")
            from ctrlaer_buffered import run_eager
            return run_eager(self, prog, use_ms=use_ms, **options)
        if mode == "buffered":
            run = self.prepare(prog, mode="buffered", use_ms=use_ms, **options).start()
            return run.wait() if block else run
        if mode is not None or options:
            raise ValueError("run mode must be buffered, eager or None; DMA options need an explicit mode")
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
        """Read integer FIFO commands from a text/byte stream; return `None`.

        Each nonblank line is `state,duration`, using decimal integers and
        millisecond durations. `END` or EOF ends input. Executed input pairs are
        echoed to standard output after enqueueing. Malformed lines propagate
        parsing errors. Call `block()` after this method to await the final
        queued output, then close the controller.
        """
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

    def prepare(self, program, mode="finite", **kwargs):
        """Prepare finite playback, or bounded-memory incremental playback.

        mode='finite' compiles the whole generator (or an explicit window).
        mode='buffered' keeps five bounded buffers and can consume an infinite
        generator. start() enables playback; wait()/poll() service it. Chunk
        transitions preserve levels and phase. A late producer raises an
        UnderrunError and applies final_mask. Arbitrary Python side effects run
        during buffered lookahead; managed effects follow playback order.

        `program` can be an iterable or a factory. A finite `CompiledProgram`
        may also be supplied in finite mode. Factory `parameters` are passed as
        keyword arguments. Durations default to milliseconds (`use_ms=True`).

        Common options are `final_mask` (default 0), `trigger` (an initial
        `Trigger`), and `poll_ms` (soft-service period, default 1 ms). Finite
        options include `window_ms`, `max_segments`, `max_words` and `names`;
        see `compile_program`. Buffered options include `buffer_words=4096`
        per buffer, `horizon_ms=20`, `history_limit=128` and
        `max_pending_effects=64`. Output count and clock must match the controller
        and current system clock. See the execution-mode guide for memory/DMA
        budgets, lookahead and side effects.

        Returns an inactive run in state `prepared`, with DMA/FIFO prefetched.
        Replaces a previous inactive run after closing it; an active run raises
        `RuntimeError`. Invalid commands/options or resource failures propagate
        before playback starts. Call `start()` explicitly.
        """
        from ctrlaer_runtime import PreparedRun
        if mode not in ("finite", "buffered"):
            raise ValueError("prepare mode must be finite or buffered")
        reuse_pool = None
        if self._prepared is not None and not self._prepared.closed:
            if self._prepared.active:
                raise RuntimeError("abort or wait for the current run before preparing another")
            previous = self._prepared
            if (mode == "buffered" and hasattr(previous, 'buffers') and
                    kwargs.get('buffer_words',4096) == previous.buffer_words):
                compiler = previous.compiler
                reuse_pool = (previous.buffers,
                              (compiler._edge_storage,compiler.edge_buffer,compiler.pack_state),
                              previous._receive)
            self._prepared.close()
        if self._sm is not None:
            self.close()
        if mode == "buffered":
            from ctrlaer_buffered import BufferedRun
            if reuse_pool is not None:
                kwargs['_reuse_pool'] = reuse_pool
            self._prepared = BufferedRun(self, program, **kwargs)
        else:
            self._prepared = PreparedRun(self, program, **kwargs)
        return self._prepared

    def start(self):
        """Start the most recently prepared run and return it.

        Raises `RuntimeError` if no run was prepared or it is not startable.
        An initial hardware trigger arms the run; it does not block this call.
        """
        if self._prepared is None:
            raise RuntimeError("prepare a program before starting")
        return self._prepared.start()

    def wait(self, timeout_ms=None):
        """Wait for prepared completion, or queued original FIFO output.

        `timeout_ms` is a positive millisecond deadline or `None`. Returns the
        prepared run, or `None` for FIFO playback. Prepared errors propagate and
        apply their final mask; see `PreparedRun.wait` and `block`.
        """
        if self._prepared is None:
            return self.block(timeout_ms)
        return self._prepared.wait(timeout_ms)

    def abort(self):
        """Stop the current engine; return its prepared run or `None` for FIFO.

        Prepared playback applies its configured final mask and stops service
        and DMA. Original FIFO playback disables its state machine and retains
        the last digital level. This does not release hardware; call `close()`.
        """
        if self._prepared is not None:
            return self._prepared.abort()
        if self._sm is not None:
            self._sm.active(0)

    def close(self):
        """Stop playback and release owned PIO/DMA resources; return `None`.

        Active/prepared DMA runs are aborted with their final mask. Completed
        runs retain their final output level. Original FIFO playback is disabled
        at its last level. Safe to call repeatedly, including from `finally`.
        """
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
