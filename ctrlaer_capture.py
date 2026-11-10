"""Finite digital traces on both Python runtimes, with optional RP2 PIO capture."""
from array import array
from ctrlaer_program import integer, number, rounded


class CaptureOverrunError(RuntimeError):
    """The capture PIO stalled because DMA could not drain its receive FIFO.

    The capture stops in state `error`. A partial trace is retained with
    `metadata['overrun']=True`; its timestamps cannot be used for waveform
    comparison. `Trace.compare` rejects such traces.
    """


class Trace:
    """Portable packed digital samples returned by capture or record loading.

    `words` is a uint32 buffer, `n_pins` is 1–16 contiguous channels, `samples`
    is the number of valid samples (possibly zero for an early abort), and
    `sample_hz` is the achieved sample rate. Each word packs `32 // n_pins`
    samples; unused bits/last-word samples are ignored. Use `levels`/`edges`
    rather than decoding the packing yourself.

    Optional `names` supplies one label per channel; defaults are CH0, CH1, etc.
    `metadata` is copied into a dictionary. Channel 0 is the least-significant
    bit of each decoded sample mask. The object references its input word buffer
    without copying: rearming its `DigitalCapture` overwrites that same memory.
    Save/copy a trace before reusing the capture when it must be retained.
    """
    def __init__(self, words, n_pins, samples, sample_hz, names=None, metadata=None):
        self.n_pins = integer(n_pins, "capture n_pins", 1, 16)
        self.samples = integer(samples, "samples", 0, 1000000000)
        self.sample_hz = number(sample_hz, "sample_hz", strict=True)
        self.words = words
        self.per_word = 32 // n_pins
        self.shift = 32 - self.per_word * n_pins
        if samples > len(words) * self.per_word:
            raise ValueError("trace buffer is shorter than its sample count")
        self.names = list(names) if names is not None else ["CH%d" % i for i in range(n_pins)]
        if len(self.names) != n_pins:
            raise ValueError("one capture name is required per channel")
        self.metadata = dict(metadata or {})

    @property
    def duration_ms(self):
        """Valid sample count divided by achieved sample rate, in milliseconds."""
        return self.samples * 1000 / self.sample_hz

    def levels(self):
        """Yield one integer channel mask per valid sample in chronological order."""
        mask = (1 << self.n_pins) - 1
        remaining = self.samples
        for word in self.words:
            for position in range(self.per_word):
                if not remaining:
                    return
                yield (word >> (self.shift + position * self.n_pins)) & mask
                remaining -= 1

    def edges(self, channel, include_initial=False):
        """Yield (sample_index, level); sample 0 is this trace's time origin."""
        integer(channel, "channel", 0, self.n_pins - 1)
        previous = None
        for sample, mask in enumerate(self.levels()):
            level = (mask >> channel) & 1
            if level != previous and (sample or include_initial):
                yield sample, level
            previous = level

    def timestamps(self, channel, include_initial=False):
        """Yield hardware-sampled transitions as (nanoseconds from sample 0, level)."""
        for sample, level in self.edges(channel, include_initial):
            yield sample * 1e9 / self.sample_hz, level

    def summary(self):
        """Return channel names, samples/rate, duration, resolution, metadata and rise counts.

        Rising-edge counts exclude the initial sample. An initial HIGH is an
        observation, not evidence of a sampled LOW-to-HIGH transition.
        """
        return {"n_pins": self.n_pins, "names": self.names, "samples": self.samples,
                "sample_hz": self.sample_hz, "duration_ms": self.duration_ms,
                "resolution_ns": 1e9 / self.sample_hz, "metadata": self.metadata,
                "rising_edges": [sum(1 for _, level in self.edges(i) if level)
                                  for i in range(self.n_pins)]}

    def write_csv(self, stream):
        """Write a transition CSV to a text stream; return `None`.

        Columns are `sample,time_ns,channel,level`, including each channel's
        initial level. Rows are channel-major for bounded-memory export, so the
        file is not globally sorted by timestamp. Time starts at sample zero.
        """
        stream.write("sample,time_ns,channel,level\n")
        # Channel-major ordering makes the export streamable on a small board.
        for channel in range(self.n_pins):
            for sample, level in self.edges(channel, True):
                stream.write("%d,%.3f,%d,%d\n" %
                             (sample, sample * 1e9 / self.sample_hz, channel, level))

    def write_vcd(self, stream):
        """Write a VCD transition trace to a text stream; return `None`.

        Uses a 1 ns timescale, rounded sample timestamps and initial levels.
        Channel names are sanitised for identifiers. Suitable for desktop VCD
        viewers; actual timing resolution remains one sample period.
        """
        stream.write("$timescale 1ns $end\n$scope module ctrlaer $end\n")
        for channel, name in enumerate(self.names):
            safe = "".join(c if c.isalnum() or c == '_' else '_' for c in name)
            stream.write("$var wire 1 c%d %s $end\n" % (channel, safe or "CH%d" % channel))
        stream.write("$upscope $end\n$enddefinitions $end\n")
        previous = None
        for sample, mask in enumerate(self.levels()):
            if mask == previous:
                continue
            stream.write("#%d\n" % rounded(sample * 1e9 / self.sample_hz))
            for channel in range(self.n_pins):
                if previous is None or (mask ^ previous) & (1 << channel):
                    stream.write("%dc%d\n" % ((mask >> channel) & 1, channel))
            previous = mask

    def compare(self, program, tolerance_ns=None, align_first_edge=True,
                channels=None, gate_wait_ticks=None, max_errors=100, require_complete=True):
        """Compare sampled transitions with one exact compiled buffer.

        channels maps logical program channels to capture channels. Alignment uses
        one common offset for every channel. Gate waits must be supplied explicitly
        in output-clock ticks, since they are not fixed by a compiled timeline.

        - `program`: a finite `CompiledProgram`.
        - `tolerance_ns`: non-negative edge tolerance; defaults to one sample.
        - `align_first_edge`: shift all expected channels by one common offset
          derived from the first usable transition. Does not fit clock drift.
        - `channels`: dictionary `{program_channel: capture_channel}`; omitted
          means identity mapping and requires matching channel counts.
        - `gate_wait_ticks`: one measured external wait per gate in program-clock
          ticks, excluding the already compiled fixed control overhead.
        - `max_errors`: maximum detailed errors retained; all mismatches are counted.
        - `require_complete`: true by default. A matching partial window remains
          inconclusive rather than passing a whole-program verification.

        Returns `passed`, `mismatches`, `compared_edges`, `alignment_ns`,
        `tolerance_ns`, `errors`, `inconclusive`, and a `coverage` dictionary.
        Coverage checks the earliest required edge through the nominal program
        end, including a possible quiet prefix omitted by triggering. Invalid
        mapping/gate data, empty captures and overrun traces raise `ValueError`.
        """
        if self.metadata.get("overrun"):
            raise ValueError("an overrun trace does not have reliable sample timestamps")
        if program.gates:
            if gate_wait_ticks is None or len(gate_wait_ticks) != len(program.gates):
                raise ValueError("comparison of gates requires one measured gate_wait_ticks per gate")
            for wait in gate_wait_ticks:
                number(wait, "gate wait")
        else:
            gate_wait_ticks = []
        if channels is None:
            if self.n_pins != program.n_pins:
                raise ValueError("specify a channel mapping for different channel counts")
            channels = {i: i for i in range(program.n_pins)}
        for source, captured in channels.items():
            integer(source, "program channel", 0, program.n_pins - 1)
            integer(captured, "capture channel", 0, self.n_pins - 1)
        if not channels or not self.samples:
            raise ValueError("comparison needs channels and captured samples")
        tolerance = 1e9 / self.sample_hz if tolerance_ns is None else number(tolerance_ns, "tolerance")

        def expected(channel):
            for tick, level in program.edges(channel):
                wait = sum(gate_wait_ticks[i] for i, gate in enumerate(program.gates)
                           if tick >= gate["resume_tick"])
                yield (tick + wait) * 1e9 / program.clock_hz, level

        offset = 0
        if align_first_edge:
            aligned = False
            for source, captured in channels.items():
                observed = next(self.timestamps(captured), None)
                if observed is None:
                    continue
                for time, level in expected(source):
                    if level == observed[1]:
                        offset = observed[0] - time
                        aligned = True
                        break
                if aligned:
                    break
        errors, mismatch_count, compared = [], 0, 0
        end_ns = (self.samples - 1) * 1e9 / self.sample_hz
        for source, captured in channels.items():
            initial_level = next(self.levels()) >> captured & 1
            def visible_edges():
                first = True
                for time, level in expected(source):
                    shifted = time + offset
                    if not 0 < shifted <= end_ns:
                        continue
                    # Sampling/alignment can place an initial HIGH transition
                    # just after sample zero even though sample zero is HIGH.
                    # Accept that one origin edge within the stated tolerance.
                    origin = first and shifted <= tolerance and level == initial_level
                    first = False
                    if not origin:
                        yield shifted, level
            wanted = visible_edges()
            measured = iter(self.timestamps(captured))
            while True:
                want, got = next(wanted, None), next(measured, None)
                if want is None and got is None:
                    break
                compared += 1
                if (want is None or got is None or want[1] != got[1]
                        or abs(want[0] - got[0]) > tolerance):
                    mismatch_count += 1
                    if len(errors) < max_errors:
                        errors.append({"channel": source, "expected": want, "observed": got})
            # Constant traces still need a level check; no transitions is not
            # evidence of a match to a differently configured constant output.
            if not any(True for _ in self.edges(captured)):
                sample_level = next(self.levels()) >> captured & 1
                expected_level = 0
                for t, level in expected(source):
                    if t + offset > 0:
                        break
                    expected_level = level
                if sample_level != expected_level:
                    mismatch_count += 1
                    if len(errors) < max_errors:
                        errors.append({"channel": source, "constant_level_mismatch": True})
        expected_end = (program.duration_ticks + sum(gate_wait_ticks)) * 1e9 / program.clock_hz + offset
        first_edge = None
        for source in channels:
            first = next(expected(source), None)
            if first is not None:
                first_edge = first[0] if first_edge is None else min(first_edge, first[0])
        # A triggered trace may intentionally omit an initial all-LOW dead-time
        # or phase delay. Require coverage of the earliest actual output edge,
        # rather than mistaking that quiet prefix for missing waveform activity.
        required_start = (0 if first_edge is None else first_edge) + offset
        complete = required_start >= -tolerance and end_ns >= expected_end - tolerance
        matched = mismatch_count == 0
        return {"passed": matched and (complete or not require_complete), "mismatches": mismatch_count,
                "compared_edges": compared, "alignment_ns": offset,
                "tolerance_ns": tolerance, "errors": errors,
                "inconclusive": matched and not complete,
                "coverage": {"complete": complete, "program_start_ns": offset,
                             "first_required_edge_ns": required_start,
                             "initial_quiet_prefix_inferred": offset < -tolerance and complete,
                             "program_end_ns": expected_end, "capture_end_ns": end_ns}}


_programs = {}
_users = {}


def _sample_rate(clock_hz, divider):
    # machine.mem32 reads signed values. Dividers >= 32768 have bit 31 set.
    fixed = (((divider >> 16) & 0xffff) or 65536) * 256 + ((divider >> 8) & 255)
    return clock_hz * 128 / fixed


def _sampler(n_pins, trigger):
    from rp2 import PIO, asm_pio
    levels = () if trigger is None else trigger.levels()
    pin_number = 0 if trigger is None else trigger.pin
    key = (n_pins, pin_number, levels)
    if key not in _programs:
        # A fixed-size loop in the assembler is unrolled at assembly time.
        @asm_pio(in_shiftdir=PIO.SHIFT_RIGHT, autopush=True,
                 push_thresh=(32 // n_pins) * n_pins, fifo_join=PIO.JOIN_RX)
        def sample():
            for level in levels:
                wait(level, gpio, pin_number)
            wrap_target()
            label("samples")
            in_(pins, n_pins)
            jmp(x_dec, "samples")
            irq(block, rel(0))
            wrap()
        _programs[key] = sample
    return key, _programs[key]


class DigitalCapture:
    """Record digital levels at regular intervals using the Pico's PIO hardware.

    It can observe an existing output on the same Pico or an external input.
    Capturing a pin does not change its GPIO function or direction. Configure
    external signals as inputs with `machine.Pin` first.

    Args:
        sm_number (int): Free PIO state-machine number: 0–7 on RP2040, 0–11 on RP2350.
            Use a different state machine from the output controller.
        base_pin (int): GPIO number of the first input, from 0 to 29.
        n_pins (int): Number of consecutive input pins, from 1 to 16. The last pin
            must not exceed GPIO29. Defaults to one channel.
        sample_hz (float): Samples per second, default 10 million. The supported
            range is system clock / 131072 through system clock / 2. The actual
            rate is reported and may differ slightly from the request.
        samples (int or None): Number of samples to record. Supply this or
            `duration_ms`, but not both.
        duration_ms (float or None): Recording duration in milliseconds, as an
            alternative to a sample count.
        trigger (Trigger or None): Input change or level to wait for before sampling.
            Its own `timeout_ms` is unused here; set `capture.wait(timeout_ms=...)`
            for an overall deadline, including time waiting for the trigger.
        names (sequence or None): One label per input channel.
        max_bytes (int): Maximum packed sample-buffer size, default 65536 (64 KiB).

    Construction reserves one PIO state machine, one DMA channel and the sample buffer.
    Call `start()` before producing the signal, then `wait()` to obtain a `Trace`.
    Invalid options or an oversized buffer raise `ValueError`; allocating hardware
    or memory can also fail. A `with` block closes the capture on exit.
    """
    def __init__(self, sm_number, base_pin, n_pins=1, sample_hz=10000000,
                 samples=None, duration_ms=None, trigger=None, names=None, max_bytes=65536):
        from machine import Pin, freq, mem32
        from rp2 import StateMachine
        from ctrlaer_runtime import claim, release
        from ctrlaer_program import Trigger
        integer(n_pins, "n_pins", 1, 16)
        integer(base_pin, "base_pin", 0, 30 - n_pins)
        number(sample_hz, "sample_hz", strict=True)
        if sample_hz < freq() / (2 * 65536):
            raise ValueError("sample rate is below the state machine clock range")
        if trigger is not None and not isinstance(trigger, Trigger):
            raise ValueError("capture trigger must be a Trigger")
        if sample_hz > freq() / 2:
            raise ValueError("sample rate exceeds half the system clock")
        if (samples is None) == (duration_ms is None):
            raise ValueError("choose samples or duration_ms")
        if duration_ms is not None:
            samples = max(1, rounded(number(duration_ms, "capture duration", strict=True) * sample_hz / 1000))
        integer(samples, "samples", 1, 1000000000)
        integer(max_bytes, "max_bytes", 4, 1000000000)
        words = (samples + (32 // n_pins) - 1) // (32 // n_pins)
        if words * 4 > max_bytes:
            raise ValueError("capture exceeds max_bytes")
        self.words = array("I")
        for _ in range(words):
            self.words.append(0)
        self.sm_id, self.base_pin, self.n_pins = sm_number, base_pin, n_pins
        self.samples, self.names, self.trigger = samples, names, trigger
        self.state, self.closed, self._registered = "preparing", False, False
        self._dma = self.sm = None
        self.trace = None
        self._key, self._program = _sampler(n_pins, trigger)
        self._user_key = (sm_number // 4, self._key)
        try:
            claim(self, sm_number)
            self.sm = StateMachine(sm_number, self._program, freq=int(2 * sample_hz), in_base=Pin(base_pin))
            self.sm.irq(handler=None)
            _users[self._user_key] = _users.get(self._user_key, 0) + 1
            self._registered = True
            address = 0x502000c8 + (sm_number // 4) * 0x100000 + (sm_number % 4) * 24
            divider = mem32[address]
            self.sample_hz = _sample_rate(freq(), divider)
            self.clock_hz = freq()
            self.arm()
        except BaseException:
            self.close()
            raise

    def arm(self):
        """Reset an inactive open capture to `prepared`; return this capture.

        Reuses its packed sample buffer, overwriting data referenced by an old
        trace on the next acquisition. Clears error/overrun/trace state. Active,
        closed or changed-clock captures raise `RuntimeError`.
        """
        from machine import Pin, freq, mem32
        from rp2 import DMA
        from ctrlaer_runtime import _close_dma
        if self.closed or self.state == "running":
            raise RuntimeError("capture must be inactive and open")
        if freq() != self.clock_hz:
            raise RuntimeError("system clock changed; create a new capture")
        self.sm.active(0)
        if self._dma is not None:
            _close_dma((self._dma,))
            self._dma = None
        self.sm.init(self._program, freq=int(2 * self.sample_hz), in_base=Pin(self.base_pin))
        base = 0x50200000 + (self.sm_id // 4) * 0x100000
        bit = 1 << (self.sm_id % 4)
        shift = base + 0xd0 + (self.sm_id % 4) * 24
        original = mem32[shift]
        # Load the finite sample counter through TX before restoring JOIN_RX.
        # Stopping in PIO avoids confusing a real overrun with a full FIFO after
        # the requested capture has already ended.
        mem32[shift] = original & ~(1 << 31)
        self.sm.put(len(self.words) * (32 // self.n_pins) - 1)
        self.sm.exec("pull()")
        self.sm.exec("mov(x, osr)")
        mem32[shift] = original
        mem32[base + 0x30] = bit
        mem32[base + 8] = bit | bit << 24
        self.overrun = False
        self.error = None
        self._dma = DMA()
        self._dma.config(read=self.sm, write=self.words, count=len(self.words),
                         ctrl=self._dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                                 treq_sel=(self.sm_id // 4) * 8 + 4 + self.sm_id % 4),
                         trigger=True)
        self.state, self.trace = "prepared", None
        return self

    def start(self):
        """Enable sampling or its trigger wait; return this capture in `running` state.

        Requires an open prepared capture and the original system clock.
        Does not wait for a trigger or for acquisition to finish.
        """
        from machine import freq
        from ctrlaer_runtime import enable_mask
        if self.state != "prepared" or self.closed:
            raise RuntimeError("capture must be prepared before starting")
        if freq() != self.clock_hz:
            raise RuntimeError("system clock changed after capture preparation")
        self.state = "running"
        enable_mask(self.sm_id // 4, 1 << (self.sm_id % 4))
        return self

    def _finish(self, complete):
        from ctrlaer_runtime import _disable_dma
        if self._dma is None:
            return
        self.sm.active(0)
        _disable_dma(1 << self._dma.channel)
        remaining = self._dma.count
        self._dma.close()
        self._dma = None
        samples = self.samples if complete else min(self.samples, (len(self.words) - remaining) * (32 // self.n_pins))
        self.trace = Trace(self.words, self.n_pins, samples, self.sample_hz, self.names,
                           {"base_pin": self.base_pin, "sm_number": self.sm_id,
                            "clock_hz": self.clock_hz, "complete": complete,
                            "overrun": self.overrun,
                            "origin": "first sampled value", "input_synchronisers": True,
                            "trigger": self.trigger.to_dict() if self.trigger else None})
        self.state = "done" if complete else "aborted"

    def poll(self):
        """Service finite completion/overrun and return the capture state string.

        `running` also includes waiting for a trigger. A receive stall stops
        capture and raises `CaptureOverrunError`; a clock change aborts and
        raises `RuntimeError`. Successful completion creates `trace` and sets
        state `done`. GPIO sampling itself runs entirely in PIO/DMA.
        """
        from machine import freq, mem32
        if self.state == "running" and freq() != self.clock_hz:
            self.abort()
            raise RuntimeError("system clock changed during capture")
        if self.state == "running":
            base = 0x50200000 + (self.sm_id // 4) * 0x100000
            bit = 1 << (self.sm_id % 4)
            if mem32[base + 8] & bit:
                self.overrun = True
                self._finish(False)
                self.state = "error"
                self.error = CaptureOverrunError("PIO capture stalled waiting for DMA buffer space")
                raise self.error
            if not self._dma.active() and mem32[base + 0x30] & bit:
                self._finish(True)
        return self.state

    def wait(self, timeout_ms=1000):
        """Wait for acquisition and return its `Trace`, or raise an execution error.

        Default deadline is 1000 ms, including the trigger wait; positive
        milliseconds or `None` for no deadline are accepted. Timeout aborts and
        raises `ExecutionTimeoutError`, retaining any complete-word partial
        trace. A closed/never-started capture raises `RuntimeError`.
        """
        from time import ticks_ms, ticks_diff, sleep_ms
        from ctrlaer_runtime import ExecutionTimeoutError
        if self.closed or self.state == "prepared":
            raise RuntimeError("start the capture before waiting")
        if timeout_ms is not None:
            number(timeout_ms, "capture timeout", strict=True)
        start = ticks_ms()
        try:
            while self.poll() == "running":
                if timeout_ms is not None and ticks_diff(ticks_ms(), start) >= timeout_ms:
                    self.abort()
                    raise ExecutionTimeoutError("capture timed out")
                sleep_ms(1)
            if self.error is not None:
                raise self.error
        except BaseException:
            self.abort()
            raise
        return self.trace

    def abort(self):
        """Stop an active/prepared capture and return its partial `Trace`.

        Complete DMA words are retained; a not-yet-pushed word is omitted. An
        already terminal capture returns its existing trace. Hardware ownership
        remains until `close()`; `arm()` permits another acquisition.
        """
        if self.state in ("prepared", "running"):
            self._finish(False)
        return self.trace

    def close(self):
        """Abort if needed and release capture PIO/DMA resources; return `None`.

        Idempotent. The trace/word buffer remains available for inspection or
        export after closing. A closed capture cannot be armed again.
        """
        from rp2 import PIO
        from ctrlaer_runtime import release, _close_dma
        if self.closed:
            return
        self.abort()
        if self.sm is not None:
            self.sm.active(0)
        if self._dma is not None:
            _close_dma((self._dma,))
            self._dma = None
        if self._registered:
            _users[self._user_key] -= 1
            if not _users[self._user_key]:
                PIO(self.sm_id // 4).remove_program(self._program)
            self._registered = False
        release(self)
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
