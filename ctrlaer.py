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
    """An endless generator of `(ON, 1000)` instructions.

    Use the exported `always_on` object without parentheses. It is one shared
    iterator; define your own generator when separate experiments need independent
    state. Durations use the units selected by `run()` or `prepare()`.
    """
    while True:
        yield ON, 1000


def _inactive():
    """A generator that yields `(OFF, 1000)` once.

    Use the exported `inactive` object without parentheses. In a `mux()` program,
    a channel stays LOW after its generator ends, so this gives an unused channel.
    The object is a shared iterator and stays exhausted after it has been read.
    """
    # Yielding once is enough: mux() yields OFF when the generator is exhausted.
    # This guarantees that mux() terminates after the last active program.
    yield OFF, 1000


inactive = _inactive()
always_on = _always_on()


def mux(progs):
    """Combine one signal generator per output into a synchronised experiment.

    Args:
        progs (sequence): Between 1 and 16 generators or other iterables. Each
            supplies `(command, duration)` pairs for one output pin. Their order
            determines the output order, starting at the controller's `base_pin`.

    Returns:
        A generator to pass to `run()`, `prepare()` or `compile_program()`.

    Examples:
        ```python
        def light():
            yield HIGH, 10
            yield OFF, 90

        def camera():
            yield OFF, 5
            yield HIGH, 1
            yield OFF, 94

        experiment = mux([light(), camera()])  # GP0 and GP1 with base_pin=0.
        ```

    Notes:
        Every channel uses the same time origin. A channel whose generator ends
        stays LOW while the remaining channels finish. Zero-duration instructions
        are skipped. A wait or CtrlAer sleep inside one channel pauses the combined
        program. For independent input waits or separate high-frequency square
        waves, see `Group` and the multiple-output tutorial.
        Invalid channel counts or signal commands raise `ValueError`.
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
    """Control the timing of one or more neighbouring GPIO pins.

    Args:
        sm_number (int): Which PIO state machine to use. Start with 0 for your first
            controller and use a different free number for another controller.
            There are 8 state machines (0–7) on RP2040 and 12 (0–11) on RP2350.
        base_pin (int): GPIO number of the first output. For example, 0 means GP0,
            which is physical pin 1 on a standard Pico.
        n_pins (int): Number of neighbouring output pins, from 1 to 16. With
            `base_pin=0, n_pins=2`, the outputs are GP0 and GP1.
        freq (float): Square-wave frequency in hertz for `ON`, `PULSE01` and
            `PULSE10`. The default is 108050 Hz. Constant HIGH/LOW instructions do
            not oscillate; `square_wave()` and `sweep()` supply their own frequency.

    Examples:
        ```python
        controller = CtrlAer(sm_number=0, base_pin=0, n_pins=2, freq=40_000)
        ```

    Notes:
        Creating the controller does not start a signal. Call `run()` to execute
        a generator, or `prepare()` to set up playback before starting it.
        Outputs must lie within GPIO0–29 and be available on the board. Two
        controllers cannot share an output pin or PIO state machine. Call `close()` in
        a `finally` block when the experiment finishes or is interrupted.
        Device execution requires MicroPython on an RP2040/RP2350 board.
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
        """The MicroPython `StateMachine` used by the original `run()` interface.

        Access allocates and enables that state machine if necessary. It is unavailable
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
        """Change the square-wave frequency used by the original `run()` interface.

        Args:
            freq (float): New frequency in hertz, greater than zero and within the
                PIO state machine's supported divider range.

        Returns:
            `None`.

        Notes:
            A running original state machine changes frequency immediately, including for
            instructions already in its queue. The hardware rounds the divider to
            an available value. This method raises `RuntimeError` while a prepared
            run is open; prepare another program with the desired signal settings.
            Invalid frequencies raise `ValueError`.
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
        """Convert milliseconds to a whole number of the controller's square-wave periods.

        Args:
            time (float): Non-negative duration in milliseconds.

        Returns:
            The period count, rounded down to an integer. At 40 kHz, 2 ms is 80 periods.

        Notes:
            This uses the controller's `freq`, not the chip clock. Invalid durations
            raise `ValueError`.
        """
        return int(_duration(time) * self._freq / 1000)
    
    def is_full(self):
        """Return whether the original PIO state machine's instruction queue is full.

        This is a low-level queue check. It does not wait or report whether playback
        has finished. It is unavailable while a prepared run is open.
        """
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 16))
    
    def is_empty(self):
        """Return whether the original PIO state machine's instruction queue is empty.

        An instruction may still be playing after leaving the queue. Use `block()`
        to wait until the output has actually finished. This is a low-level queue
        check and is unavailable while a prepared run is open.
        """
        return bool(mem32[0x50200004 + self.pio_index * 0x100000] & (self.bit_mask << 24))
    
    def block(self, timeout_ms=None):
        """Wait until the preceding output has finished.

        Args:
            timeout_ms (float or None): Maximum wait in milliseconds, greater than
                zero. The default `None` has no time limit.

        Returns:
            `None` for the original `run()` interface, or the current prepared run.

        Notes:
            Call this after a finite `run()` when the next Python operation must wait
            for the signal to finish. It waits for the last instruction's duration,
            not just for the queue to become empty. With a prepared run it delegates
            to that run's `wait()` method. An original-interface timeout raises
            `RuntimeError` and leaves that state machine running; abort or close it explicitly.
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
        """Run a generator of `(command, duration)` instructions.

        Args:
            prog (iterable): The experiment's instructions. The explicit buffered and
                eager options also accept a function that returns a fresh generator.
            block (bool): With the original interface, wait for queue space as commands
                are submitted. This does not wait for the final output to finish;
                call `controller.block()` afterwards. The default is `True`.
            use_ms (bool): Interpret durations as milliseconds when `True` (the
                default), or as whole periods of the controller's `freq` when `False`.
            mode (str or None): Current execution option. `None` keeps the original
                integer-command interface; `"buffered"` supports long programs with
                adjustable square waves and hardware waits; `"eager"` waits for each
                instruction to finish before continuing the experiment function. See the execution guide
                for the consequences of each option.
            options: Preparation options passed to the selected buffered/eager run.
                Examples are `final_mask`, `trigger`, `buffer_words` and `parameters`.

        Returns:
            With the original interface and `block=True`, `None` once every instruction
            has been submitted. With `block=False`, the unfinished iterator when the
            queue has no room, or `None` when it is exhausted. Keep and resubmit that
            iterator to continue. Explicit buffered/eager execution returns a run object;
            buffered `block=False` returns while output is active. Eager requires
            `block=True` and returns after a finite experiment finishes.

        Notes:
            The original interface accepts the integer commands such as `HIGH`, `OFF`
            and `ON`. `square_wave()`, `sweep()` and `wait_for()` use `prepare()`,
            buffered execution or eager execution. Invalid options raise `ValueError`. Buffered/eager
            errors stop playback with `final_mask` and propagate to the caller.
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
        """Set up an experiment so that it can be started explicitly.

        Args:
            program (iterable or callable): Instructions, a function returning them,
                or an already compiled program for finite playback.
            mode (str): `"finite"` (default) calculates and stores the whole experiment
                before starting. `"buffered"` calculates later sections during playback,
                allowing an experiment to repeat indefinitely.
            use_ms (bool): Durations are in milliseconds by default. `False` uses
                whole periods of the controller's `freq`.
            final_mask (int): Output levels at completion, abort or error. Bit 0 selects
                the first output, bit 1 the second, and so on. The default 0 leaves all LOW.
            trigger (Trigger or None): An input condition to wait for before the first
                output. The default starts when `start()` is called.
            parameters (dict or None): Keyword arguments for a generator function.
            poll_ms (int): Interval between Python's completion/error checks, in
                milliseconds. Default: 1. These checks do not set waveform timing.
            window_ms (float or None): For finite preparation, stop at this duration
                when examining a longer or endless experiment.
            max_segments (int): Finite limit on the number of instructions read.
                Default: 100000.
            max_words (int): Finite limit on stored 32-bit words. Default: 32768.
            names (sequence or None): Optional channel labels for a finite program.
            buffer_words (int): Capacity of each buffered waveform block. A power of
                two from 32 to 8192; default 4096. Default waveform storage is 96 KiB.
            horizon_ms (float): Target playback time per buffered block, default
                20 ms. A constant level is kept whole even if it lasts longer;
                fast-changing signals can fill a block sooner.
            history_limit (int): Number of recent buffered gate/message observations
                to retain. Default: 128. Use 0 to retain none.
            max_pending_effects (int): Maximum queued CtrlAer sleeps, messages and waits
                during one generator advance. Default: 64. Yield regularly between them.
            clock_hz (int): Compilation clock in hertz; normally omit it so the current
                chip clock is used. It must match the device when played.
            n_pins (int): Compilation channel count; normally omit it so the controller's
                output count is used. It must match the controller.
            freq (float): Compilation frequency for integer commands; normally omit it
                to use the controller's `freq`.

        Returns:
            A run object in the `prepared` state. No output starts until `start()`.

        Notes:
            With the default finite preparation, use a generator that finishes or supply
            a `window_ms` limit. A repeating experiment needs buffered preparation.
            Finite packet data must fit memory; buffered playback reserves more working
            memory and requires Python to keep supplying later sections in time.
            Preparing a new program closes an inactive previous run. An active previous
            run must finish or be aborted first. Invalid commands, memory limits or
            resource conflicts are reported before output starts. Compile options
            cannot change an already compiled program's clock, channels or settings.
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
        """Start the most recently prepared experiment and return its run.

        Prepare a program first. An input trigger can delay its first output after
        this call returns. Calling this with no prepared program, a closed run or
        an invalid run state raises `RuntimeError`.
        """
        if self._prepared is None:
            raise RuntimeError("prepare a program before starting")
        return self._prepared.start()

    def wait(self, timeout_ms=None):
        """Wait for the controller's current output to finish.

        `timeout_ms` is an optional positive deadline in milliseconds. With a prepared
        run, this returns that run and propagates its errors. With the original
        interface, it behaves like `block()` and returns `None`.
        """
        if self._prepared is None:
            return self.block(timeout_ms)
        return self._prepared.wait(timeout_ms)

    def abort(self):
        """Stop the current output immediately from Python.

        A prepared or buffered run applies its `final_mask` and is returned. The
        original `run()` state machine stops at its last pin level and returns `None`.
        The hardware remains reserved until `close()`.
        """
        if self._prepared is not None:
            return self._prepared.abort()
        if self._sm is not None:
            self._sm.active(0)

    def close(self):
        """Stop the controller and release its timing and DMA resources.

        Returns `None`. Safe to call more than once. An active prepared or buffered
        run stops with its `final_mask`; a completed run keeps its final levels.
        The original `run()` interface keeps the last pin level when stopped. Set
        those pins LOW explicitly when that is the required shutdown state.
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


from ctrlaer_commands import square_wave, sweep, sleep, print, wait_for, _Carrier, _Signal, _Parallel, _advance
from ctrlaer_program import Trigger, compile_program
from ctrlaer_capture import Trace, DigitalCapture


class Group:
    """Start separately prepared controllers together.

    Args:
        runs (iterable): Prepared run objects from different controllers. Each
            controller needs its own output pins and PIO state machine.

    Returns:
        A group with `start()`, `wait()`, `abort()`, `rearm()` and `close()` methods.

    Notes:
        Start with `mux()` when the outputs can share one controller. A group is
        useful when separate square waves cannot share that state machine's timing, or
        when one controller needs to wait while another continues. It uses more
        hardware resources. See the multiple-output guide before choosing it.
    """
    def __new__(cls, runs):
        from ctrlaer_runtime import PreparedGroup
        return PreparedGroup(runs)
