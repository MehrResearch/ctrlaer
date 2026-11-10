"""MicroPython RP2 execution of prepared CtrlAer programs.

Output timing and edge waits run in PIO. A 1 ms soft timer supervises completion,
underruns and gate timeouts; it is not part of the output timing path.
"""
from array import array
import sys
from builtins import print as _print
from machine import Pin, Timer, freq, mem32, disable_irq, enable_irq
from rp2 import PIO, StateMachine, DMA, asm_pio
from time import ticks_us, ticks_ms, ticks_diff, sleep_ms

from ctrlaer_program import CompiledProgram, compile_program, integer, number

_owners = {}
_pins = {}
_engines = {}
_engine_users = {}


class UnderrunError(RuntimeError):
    """Playback data or gate/effect reception missed a hardware deadline.

    The run enters `error`, stops its DMA and applies `final_mask` when serviced.
    Buffered guards prevent replay of stale data; final-mask response still has
    interpreter/service latency.
    """


class ExecutionTimeoutError(RuntimeError):
    """A `wait(timeout_ms=...)` deadline expired before execution completed.

    The run stops with its final mask. The timeout is a software deadline in
    milliseconds; external gate waiting counts towards it.
    """


class GateTimeoutError(ExecutionTimeoutError):
    """A GPIO gate exceeded its configured `timeout_ms` after its entry was serviced.

    The error message identifies the failed gate. Gate history contains service-
    observed entry/exit times; these are not hardware edge timestamps. The run
    stops with its final mask. `timeout_ms=None` disables a gate's deadline.
    """


def pio_base(sm_number):
    return 0x50200000 + (sm_number // 4) * 0x100000


def claim(owner, sm_number, base_pin=None, n_pins=0):
    count = 12 if "RP2350" in sys.implementation._machine else 8
    integer(sm_number, "state machine", 0, count - 1)
    if sm_number in _owners and _owners[sm_number] is not owner:
        raise RuntimeError("state machine is already owned by CtrlAer")
    if mem32[pio_base(sm_number)] & (1 << (sm_number % 4)):
        raise RuntimeError("state machine is already active")
    if base_pin is not None:
        for pin in range(base_pin, base_pin + n_pins):
            if pin in _pins and _pins[pin] is not owner:
                raise RuntimeError("output GPIO is already owned by CtrlAer")
    _owners[sm_number] = owner
    if base_pin is not None:
        for pin in range(base_pin, base_pin + n_pins):
            _pins[pin] = owner


def release(owner):
    for key in list(_owners):
        if _owners[key] is owner:
            del _owners[key]
    for key in list(_pins):
        if _pins[key] is owner:
            del _pins[key]


def _engine(n_pins):
    if n_pins not in _engines:
        @asm_pio(out_init=(PIO.OUT_LOW,) * n_pins, out_shiftdir=PIO.SHIFT_RIGHT)
        def packets():
            wrap_target()
            pull()
            out(exec, 16)
            pull()
            mov(x, osr)
            label("delay")
            jmp(x_dec, "delay")
            wrap()
        _engines[n_pins] = packets
    return _engines[n_pins]


def enable_mask(pio, mask):
    # Atomic SET alias: enable all selected machines and restart their dividers.
    mem32[0x50202000 + pio * 0x100000] = mask | (mask << 8)


def enable_group(masks):
    if len(masks) == 1 or "RP2350" not in sys.implementation._machine:
        for pio, mask in masks.items():
            enable_mask(pio, mask)
        return
    # RP2350 can operate on both neighbouring PIO blocks in the same bus write.
    anchor = min(masks)
    next_mask, previous_mask = masks.get((anchor + 1) % 3, 0), masks.get((anchor - 1) % 3, 0)
    address = 0x50200000 + anchor * 0x100000
    irq_state = disable_irq()
    try:
        enabled = mem32[address] & 15
        mem32[address] = (enabled | masks[anchor] | masks[anchor] << 8 |
                          next_mask << 20 | previous_mask << 16 | 1 << 24 | 1 << 26)
    finally:
        enable_irq(irq_state)


class PreparedRun:
    """Finite PIO/DMA playback returned by `CtrlAer.prepare(mode='finite')`.

    Construct through the controller. `program` is its `CompiledProgram`, and
    `sm` is the low-level MicroPython state machine. `poll_ms` is a soft-service
    period from 1 to 1000 ms, default 1; it supervises errors, completion and
    notifications without timing the output edges. Other options are forwarded
    to `compile_program` when the input is a generator/factory.

    `state` progresses from `prepared` to `armed`/`running`/`waiting`, then `done`,
    `aborted` or `error`. `error` retains the exception or `None`; `closed` records
    resource release. `gate_history`/`effect_history` contain service observations.
    A run supports `with` for closing on exit. Preparation/rearming initialises
    outputs LOW; completion and abort use `program.final_mask`.
    """
    TERMINAL = ("done", "aborted", "error")

    def __init__(self, controller, program, poll_ms=1, **compile_options):
        integer(poll_ms, "poll_ms", 1, 1000)
        self.controller = controller
        self.sm_id, self.base_pin = controller._sm_id, controller.base_pin
        self.pio_index, self.local_sm = self.sm_id // 4, self.sm_id % 4
        self.base, self.bit = pio_base(self.sm_id), 1 << self.local_sm
        self.poll_ms = poll_ms
        self.state, self.error, self.closed = "preparing", None, False
        self.sm = self._tx = self._rx = self._timer = None
        self._registered = False
        self._servicing = False
        self._group = None
        self.elapsed_us = 0
        if isinstance(program, CompiledProgram):
            if compile_options:
                raise ValueError("compile options cannot change an already compiled program")
            self.program = program
        else:
            compile_options.setdefault("clock_hz", freq())
            compile_options.setdefault("n_pins", controller.n_pins)
            compile_options.setdefault("freq", controller._freq)
            self.program = compile_program(program, **compile_options)
        if self.program.n_pins != controller.n_pins:
            raise ValueError("compiled channel count does not match controller")
        self.program.validate()
        if self.program.clock_hz != freq():
            raise ValueError("compiled clock does not match machine.freq()")
        outputs = range(self.base_pin, self.base_pin + self.program.n_pins)
        if any(g["pin"] in outputs for g in self.program.gates):
            raise ValueError("a gate cannot wait on this controller's own output GPIO")
        self._program = _engine(self.program.n_pins)
        self._key = (self.pio_index, self.program.n_pins)
        claim(self, self.sm_id, self.base_pin, self.program.n_pins)
        try:
            self.sm = StateMachine(self.sm_id, self._program, freq=self.program.clock_hz,
                                   out_base=Pin(self.base_pin))
            _engine_users[self._key] = _engine_users.get(self._key, 0) + 1
            self._registered = True
            self._configure()
        except Exception:
            self.close()
            raise

    @property
    def active(self):
        """Whether state is `armed`, `running` or `waiting`; does not poll hardware."""
        return self.state in ("armed", "running", "waiting")

    def _stop_dma(self):
        for name in ("_tx", "_rx"):
            channel = getattr(self, name)
            if channel is not None:
                channel.active(0)
                channel.close()
                setattr(self, name, None)

    def _stop_timer(self):
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.deinit()

    def _configure(self):
        self.sm.active(0)
        self._stop_dma()
        self.sm.init(self._program, freq=self.program.clock_hz, out_base=Pin(self.base_pin))
        self.sm.irq(handler=None)
        mem32[self.base + 0x30] = self.bit
        mem32[self.base + 8] = self.bit << 24 | self.bit
        self._event_buffer = array("I", [0] * len(self.program.events))
        self._seen = 0
        self._gate = None
        self._gate_since = None
        self.gate_history = []
        self.effect_history = []
        self._rx = None
        if self._event_buffer:
            self._rx = DMA()
            self._rx.config(read=self.sm, write=self._event_buffer,
                            count=len(self._event_buffer),
                            ctrl=self._rx.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                                   treq_sel=self.pio_index * 8 + 4 + self.local_sm),
                            trigger=True)
        self._tx = DMA()
        self._tx.config(read=self.program.words, write=self.sm, count=len(self.program.words),
                        ctrl=self._tx.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                               high_pri=True,
                                               treq_sel=self.pio_index * 8 + self.local_sm),
                        trigger=True)
        # The PIO remains disabled during preparation. Preload complete packets.
        start = ticks_ms()
        while self.sm.tx_fifo() < min(4, len(self.program.words)):
            if ticks_diff(ticks_ms(), start) > 100:
                raise RuntimeError("DMA did not prefill the transmit FIFO")
        self.state, self.error, self.elapsed_us = "prepared", None, 0

    def _arm_service(self):
        if self.closed or self.state != "prepared":
            raise RuntimeError("run must be prepared before starting")
        if freq() != self.program.clock_hz:
            raise RuntimeError("system clock changed after preparation")
        self._last_us = ticks_us()
        self.state = "armed" if self.program.gates and self.program.gates[0]["nominal_tick"] == 0 else "running"
        self._timer = Timer(-1)
        self._timer.init(period=self.poll_ms, mode=Timer.PERIODIC, callback=self._timer_service)

    def start(self):
        """Enable the prepared PIO and start supervision; return this run.

        Does not wait for an initial GPIO trigger. Requires an open run in state
        `prepared` and the same system clock used for compilation. Invalid state
        or a changed clock raises `RuntimeError` before enabling playback.
        """
        self._arm_service()
        enable_mask(self.pio_index, self.bit)
        return self

    def _timer_service(self, timer):
        try:
            self.poll()
        except Exception as error:
            self._fail(error)

    def _read_gate_events(self):
        if self._rx is None:
            return
        available = len(self._event_buffer) - self._rx.count
        while self._seen < available:
            marker = self._event_buffer[self._seen]
            if marker != self._seen + 1:
                raise RuntimeError("gate event stream is corrupt")
            self._seen += 1
            event = self.program.events[marker - 1]
            if event['kind'] == 'print':
                self.effect_history.append({'kind': 'print', 'text': event['text'],
                                            'observed_elapsed_us': self.elapsed_us})
                _print(event['text'], end='')
                continue
            index = event['gate']
            entering = event['kind'] == 'gate_enter'
            observed = ticks_ms()
            self.gate_history.append({"gate": index, "entering": entering,
                                      "observed_elapsed_us": self.elapsed_us})
            if entering:
                self._gate, self._gate_since = index, observed
                self.state = "armed" if index == 0 and self.program.gates[index]["nominal_tick"] == 0 else "waiting"
            else:
                self._gate = self._gate_since = None
                self.state = "running"

    def poll(self):
        """Service completion, markers and errors once; return the state string.

        Output edges and GPIO waits execute in PIO. This method services print
        effects and software timeouts, recording any execution exception in
        `error` and applying the final mask. Terminal/in-progress service calls
        return immediately. Use `wait()` to propagate a recorded exception.
        """
        if not self.active or self._servicing:
            return self.state
        self._servicing = True
        try:
            now = ticks_us()
            self.elapsed_us += ticks_diff(now, self._last_us)
            self._last_us = now
            self._read_gate_events()
            if mem32[self.base + 0x30] & self.bit:
                # The final marker may have been produced while Python was
                # consuming earlier markers. Keep RX DMA alive until it drains.
                self._read_gate_events()
                if self._rx is not None and self._rx.count:
                    return self.state
                self.sm.active(0)
                self._stop_dma()
                self._stop_timer()
                self.state = "done"
            elif freq() != self.program.clock_hz:
                self._fail(RuntimeError("system clock changed during execution"))
            elif mem32[self.base + 8] & (self.bit << 24):
                self._fail(UnderrunError("PIO stalled waiting for transmit data"))
            elif mem32[self.base + 8] & self.bit:
                self._fail(UnderrunError("PIO stalled waiting for gate-event buffer space"))
            elif self._gate is not None:
                timeout = self.program.gates[self._gate]["timeout_ms"]
                if timeout is not None and ticks_diff(ticks_ms(), self._gate_since) >= timeout:
                    self._fail(GateTimeoutError("gate %d timed out" % self._gate))
        except Exception as error:
            self._fail(error)
        finally:
            self._servicing = False
        return self.state

    def _drive_final(self):
        if self.sm is None:
            return
        self.sm.active(0)
        self.sm.restart()
        # Changing FIFO join mode clears stale words without changing GPIO levels.
        shift = self.base + 0xd0 + self.local_sm * 24
        original = mem32[shift]
        mem32[shift] = original ^ (1 << 31)
        mem32[shift] = original
        self.sm.put(self.program.final_mask)
        self.sm.exec("pull()")
        self.sm.exec("out(pins, %d)" % self.program.n_pins)

    def _fail(self, error):
        if self.state == "error":
            return
        self.error = error
        self._stop_timer()
        self._stop_dma()
        self._drive_final()
        self.state = "error"
        if self._group is not None:
            self._group._member_failed(self)

    def wait(self, timeout_ms=None):
        """Service playback until terminal, returning this run or raising its error.

        `timeout_ms` is positive milliseconds, or `None` for no overall deadline.
        It includes external gate waiting. Expiry records/raises
        `ExecutionTimeoutError`, stops DMA and applies the final mask. A gate's
        own timeout is independent. Waiting on a never-started/closed run raises
        `RuntimeError`. An already completed run returns immediately.
        """
        if self.closed or self.state in ("preparing", "prepared"):
            raise RuntimeError("start the prepared run before waiting")
        if timeout_ms is not None:
            number(timeout_ms, "wait timeout", strict=True)
        start = ticks_ms()
        while self.active:
            self.poll()
            if self.active and timeout_ms is not None and ticks_diff(ticks_ms(), start) >= timeout_ms:
                self._fail(ExecutionTimeoutError("execution wait timed out"))
            if self.active:
                sleep_ms(self.poll_ms)
        if self.error is not None:
            raise self.error
        return self

    def abort(self):
        """Stop playback/DMA/service and apply the final mask; return this run.

        Does not release PIO ownership, so an inactive open run can be rearmed.
        A completed run remains completed. A closed run raises `RuntimeError`.
        """
        if self.closed:
            raise RuntimeError("run is closed")
        if self.state == "done":
            return self
        self._stop_timer()
        self._stop_dma()
        self._drive_final()
        self.state = "aborted"
        return self

    def rearm(self):
        """Reset an inactive open run for replay and return it in `prepared` state.

        Reuses the same finite packed waveform without advancing its original
        generator again. Clears histories/error/timing and initialises output
        LOW. Active or closed runs raise `RuntimeError`; call `start()` to replay.
        """
        if self.closed or self.active:
            raise RuntimeError("only an inactive open run can be rearmed")
        self._stop_timer()
        self._configure()
        return self

    def status(self):
        """Service once and return a dictionary snapshot of runtime state.

        Keys are `state`, `elapsed_us`, `error` (message or `None`), `underrun`,
        `gate` (current gate index or `None`), `gate_history`, and `effect_history`.
        Elapsed microseconds are service-observed wall time, including gates;
        histories are lists of observations, not hardware-sampled timestamps.
        Buffered subclasses add queue/production statistics. Their service can
        advance the generator and execute lookahead side effects.
        """
        self.poll()
        return {"state": self.state, "elapsed_us": self.elapsed_us,
                "error": str(self.error) if self.error else None,
                "underrun": isinstance(self.error, UnderrunError),
                "gate": self._gate, "gate_history": self.gate_history,
                "effect_history": self.effect_history}

    def close(self):
        """Abort if necessary, then release DMA/PIO ownership; return `None`.

        Idempotent. Completed output keeps its final mask. Closing prevents
        starting/rearming this object; prepare another run to use the controller.
        """
        if self.closed:
            return
        if self.active or self.state in ("prepared", "preparing"):
            self.abort()
        self._stop_timer()
        self._stop_dma()
        if self.sm is not None:
            self.sm.active(0)
        if self._registered:
            users = _engine_users[self._key] - 1
            _engine_users[self._key] = users
            if not users:
                PIO(self.pio_index).remove_program(self._program)
            self._registered = False
        release(self)
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()


class PreparedGroup:
    """Coordinated prepared runs, constructed with `Group(runs)`.

    `runs` must be a nonempty iterable of distinct, open prepared state machines
    using the same system clock. Finite and buffered runs expose the same
    lifecycle. Every member retains its own output pins/final mask.

    Within one PIO block the start enable/divider reset is atomic. RP2350 also
    supports an atomic start across its PIO blocks. Cross-block RP2040 members
    require matching initial hardware gates; assert the common trigger only
    after `start()` returns. An error in one member aborts active peers. Supports
    `with` for closing every member on exit; `runs` is a tuple of members.
    """
    def __init__(self, runs):
        self.runs = tuple(runs)
        if not self.runs or len(set(run.sm_id for run in self.runs)) != len(self.runs):
            raise ValueError("group needs distinct prepared state machines")
        if len(set(run.program.clock_hz for run in self.runs)) != 1:
            raise ValueError("group clocks must agree")
        self.masks = {}
        for run in self.runs:
            self.masks[run.pio_index] = self.masks.get(run.pio_index, 0) | run.bit
        if len(self.masks) > 1 and "RP2350" not in sys.implementation._machine:
            common = None
            for run in self.runs:
                gates = run.program.gates
                if not gates or gates[0]["nominal_tick"] != 0:
                    raise ValueError("groups spanning PIO blocks need the same initial hardware trigger")
                gate = gates[0]
                signature = (gate['pin'], gate['edge'], gate['level'])
                if common is not None and common != signature:
                    raise ValueError("cross-block triggers must agree")
                common = signature

    def start(self):
        """Start all prepared members together and return this group.

        Every member must be open and in `prepared` state, with no conflicting
        group membership. This arms services before the atomic enable operation.
        External triggers can still delay output. Invalid states raise
        `RuntimeError` and any member start failure stops active peers.
        """
        if any(run.closed or run.state != "prepared" for run in self.runs):
            raise RuntimeError("every group member must be prepared")
        if any(run._group is not None and run._group is not self for run in self.runs):
            raise RuntimeError("a run already belongs to another group")
        try:
            for run in self.runs:
                run._group = self
                run._arm_service()
            enable_group(self.masks)
        except Exception:
            self.abort()
            raise
        return self

    def _member_failed(self, member):
        # Called by the same service that discovers a member fault, even when
        # application code is doing measurements instead of calling wait().
        for run in self.runs:
            if run is not member and not run.closed and (run.active or run.state == "prepared"):
                run.abort()

    def wait(self, timeout_ms=None):
        """Wait for all members, returning this group or propagating a member error.

        `timeout_ms` is a positive overall group deadline in milliseconds, or
        `None`. A member error aborts other active members. Deadline expiry uses
        `ExecutionTimeoutError`; each stopped member applies its own final mask.
        Members may finish at different times.
        """
        if timeout_ms is not None:
            number(timeout_ms, "group timeout", strict=True)
        if any(run.state == "prepared" for run in self.runs):
            raise RuntimeError("start the group before waiting")
        start = ticks_ms()
        while True:
            for run in self.runs:
                run.poll()
                if run.error is not None:
                    self.abort()
                    raise run.error
            if not any(run.active for run in self.runs):
                return self
            if timeout_ms is not None and ticks_diff(ticks_ms(), start) >= timeout_ms:
                self.abort()
                raise ExecutionTimeoutError("group wait timed out")
            sleep_ms(1)

    def rearm(self):
        """Rearm every inactive open member and return this group.

        Finite members replay their stored packets. Buffered members need a
        generator factory, so a fresh iterator can be made. Active/closed members
        or an unrewindable buffered iterator raise `RuntimeError`.
        """
        if any(run.closed or run.active for run in self.runs):
            raise RuntimeError("every group member must be inactive and open")
        for run in self.runs:
            run.rearm()
        return self

    def abort(self):
        """Abort every member with its own final mask and return this group."""
        for run in self.runs:
            if not run.closed and (run.active or run.state == "prepared"):
                run.abort()
        return self

    def close(self):
        """Close every member and release its hardware; return `None`. Idempotent."""
        for run in self.runs:
            run.close()
            if run._group is self:
                run._group = None

    def status(self):
        """Return a list of member `status()` snapshots, servicing each member."""
        return [run.status() for run in self.runs]

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
