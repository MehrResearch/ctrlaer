"""Portable, bounded compilation of generator commands to timed PIO packets.

No hardware is imported here. Times in the packed program are system-clock ticks.
The device and desktop preview both consume the same packed program.
"""
from array import array
from math import isfinite, sqrt, floor
from ctrlaer_commands import _Carrier, _Signal, _Parallel, _advance, _invoke

MIN_TICKS = 6
CONTROL_TICKS = 512  # Pace control bursts for DMA buffers in RP2350 PSRAM.
MAX_TICKS = 0xffffffff + MIN_TICKS
OUT_PINS = 0x6000
WAIT_GPIO = 0x2000
MOV_ISR_OSR = 0xa0c7
PUSH_BLOCK = 0x8020
IRQ_DONE = 0xc030  # irq(block, rel(0)): one completion flag per state machine.


def number(value, name, minimum=0, strict=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < minimum or (strict and value == minimum)):
        raise ValueError("%s must be finite and %s %s" %
                         (name, ">" if strict else ">=", minimum))
    return value


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("%s must be an integer from %s to %s" % (name, minimum, maximum))
    return value


def rounded(value):
    return int(value + 0.5)


class Trigger:
    """GPIO edge or level wait. Edge waits require the opposite level first.

    timeout_ms is supervised by the runtime service; the edge itself is observed
    by PIO. None permits an unbounded wait.
    """
    def __init__(self, pin, edge="rising", level=None, timeout_ms=None):
        self.pin = integer(pin, "trigger pin", 0, 29)
        if level is not None:
            integer(level, "trigger level", 0, 1)
            if edge not in (None, "rising"):
                raise ValueError("choose an edge or a level")
            edge = None
        elif edge not in ("rising", "falling"):
            raise ValueError("edge must be rising or falling")
        if timeout_ms is not None:
            number(timeout_ms, "trigger timeout", strict=True)
        self.edge, self.level, self.timeout_ms = edge, level, timeout_ms

    def levels(self):
        if self.level is not None:
            return (self.level,)
        return (0, 1) if self.edge == "rising" else (1, 0)

    def to_dict(self):
        return {"pin": self.pin, "edge": self.edge, "level": self.level,
                "timeout_ms": self.timeout_ms}


class CompiledProgram:
    """Packed immutable-by-contract program, suitable for DMA and offline replay."""
    def __init__(self, words, clock_hz, n_pins, names, duration_ticks, gates,
                 pulse_counts, final_mask, quantisation_ns, parameters, windowed,
                 requested_ticks=0, control_ticks=0, events=None, annotations=None):
        self.words, self.clock_hz, self.n_pins = words, clock_hz, n_pins
        self.names, self.duration_ticks, self.gates = names, duration_ticks, gates
        self.pulse_counts, self.final_mask = pulse_counts, final_mask
        self.quantisation_ns, self.parameters, self.windowed = quantisation_ns, parameters, windowed
        self.requested_ticks, self.control_ticks = requested_ticks, control_ticks
        if events is None:
            events = []
            for index in range(len(gates)):
                events.extend(({"kind": "gate_enter", "gate": index},
                               {"kind": "gate_exit", "gate": index}))
        self.events, self.annotations = events, list(annotations or [])

    @property
    def duration_ms(self):
        """Nominal duration; external gate waiting time is additional."""
        return self.duration_ticks * 1000 / self.clock_hz

    def segments(self):
        """Yield (start_tick, mask, ticks); gate waits have zero nominal wait time."""
        elapsed = 0
        output = OUT_PINS | self.n_pins
        for i in range(0, len(self.words), 2):
            instruction = self.words[i] & 0xffff
            if instruction == IRQ_DONE:
                break
            ticks = self.words[i + 1] + MIN_TICKS
            if instruction == output:
                yield elapsed, self.words[i] >> 16, ticks
            elapsed += ticks

    def edges(self, channel):
        integer(channel, "channel", 0, self.n_pins - 1)
        previous = 0
        for start, mask, ticks in self.segments():
            level = (mask >> channel) & 1
            if level != previous:
                yield start, level
                previous = level

    def summary(self):
        return {"clock_hz": self.clock_hz, "n_pins": self.n_pins, "names": self.names,
                "duration_ticks": self.duration_ticks, "duration_ms": self.duration_ms,
                "gate_wait_time_additional": bool(self.gates), "gates": self.gates,
                "events": self.events, "annotations": self.annotations,
                "pulse_counts": self.pulse_counts, "final_mask": self.final_mask,
                "max_edge_quantisation_ns": self.quantisation_ns,
                "minimum_interval_ticks": MIN_TICKS, "buffer_bytes": len(self.words) * 4,
                "parameters": self.parameters, "windowed": self.windowed,
                "requested_duration_ms": self.requested_ticks * 1000 / self.clock_hz,
                "control_overhead_ticks": self.control_ticks,
                "duration_quantisation_ns": (self.duration_ticks - self.control_ticks - self.requested_ticks) * 1e9 / self.clock_hz}

    def validate(self):
        """Validate the executable packet stream and its gate/completion metadata."""
        integer(self.n_pins, "n_pins", 1, 16)
        integer(self.clock_hz, "clock_hz", 1, 1000000000)
        integer(self.final_mask, "final_mask", 0, (1 << self.n_pins) - 1)
        if len(self.words) < 4 or len(self.words) % 2:
            raise ValueError("program needs complete command/delay packets")
        for word in self.words:
            integer(word, "packed word", 0, 0xffffffff)
        if self.words[-2] != IRQ_DONE or self.words[-1] != 0:
            raise ValueError("program must end with a completion instruction")
        final_tail = self.words[-3]
        if self.words[-4] != (OUT_PINS | self.n_pins | self.final_mask << 16) or not 0 <= final_tail < MIN_TICKS:
            raise ValueError("program must apply its final mask before completion")
        elapsed, gate_index, position, event_index = 0, 0, 0, 0
        pulses, previous = [0] * self.n_pins, 0
        control_ticks = 0
        while position < len(self.words) - 4:
            instruction = self.words[position] & 0xffff
            if instruction == OUT_PINS | self.n_pins:
                mask = self.words[position] >> 16
                integer(mask, "packed mask", 0, (1 << self.n_pins) - 1)
                rising = mask & ~previous
                for channel in range(self.n_pins):
                    pulses[channel] += rising >> channel & 1
                previous = mask
                elapsed += self.words[position + 1] + MIN_TICKS
                position += 2
                continue
            if instruction != MOV_ISR_OSR or event_index >= len(self.events):
                raise ValueError("unexpected PIO instruction in packed program")
            event = self.events[event_index]
            expected = [MOV_ISR_OSR | ((event_index + 1) << 16), PUSH_BLOCK]
            if event['kind'] == 'gate_enter':
                if gate_index >= len(self.gates) or event['gate'] != gate_index:
                    raise ValueError("gate event metadata is inconsistent")
                gate = self.gates[gate_index]
                trigger = Trigger(gate['pin'], gate['edge'], gate['level'], gate['timeout_ms'])
                if elapsed != gate['nominal_tick']:
                    raise ValueError("gate timing metadata disagrees with packed program")
                expected.extend(WAIT_GPIO | level << 7 | trigger.pin for level in trigger.levels())
                if event_index + 1 >= len(self.events) or self.events[event_index + 1] != {'kind': 'gate_exit', 'gate': gate_index}:
                    raise ValueError("gate exit event is missing")
                expected.extend((MOV_ISR_OSR | ((event_index + 2) << 16), PUSH_BLOCK))
            elif event['kind'] != 'print' or not isinstance(event.get('text'), str):
                raise ValueError("unknown effect event")
            for command in expected:
                if position >= len(self.words) - 4 or self.words[position] != command:
                    raise ValueError("invalid packed effect protocol")
                elapsed += self.words[position + 1] + MIN_TICKS
                control_ticks += self.words[position + 1] + MIN_TICKS
                position += 2
            if event['kind'] == 'gate_enter':
                if elapsed != gate['resume_tick']:
                    raise ValueError("gate resume metadata disagrees with packed program")
                gate_index += 1
                event_index += 2
            else:
                event_index += 1
        if final_tail:
            rising = self.final_mask & ~previous
            for channel in range(self.n_pins):
                pulses[channel] += rising >> channel & 1
        if (elapsed + final_tail != self.duration_ticks or gate_index != len(self.gates)
                or event_index != len(self.events) or pulses != self.pulse_counts):
            raise ValueError("program statistics disagree with packed program")
        return self


class _Writer:
    def __init__(self, clock_hz, n_pins, max_words, final_mask):
        self.clock_hz, self.n_pins, self.max_words = clock_hz, n_pins, max_words
        self.final_mask, self.words = final_mask, array("I")
        self.elapsed = 0
        self.gates = []
        self.events, self.annotations = [], []
        self.pulses = [0] * n_pins
        self.last_mask = 0
        self.pending_mask, self.pending_ticks = None, 0
        self.quantisation_ns = 0
        self.requested_ticks, self.control_ticks, self.residual = 0, 0, 0

    def duration(self, exact_ticks, limit=None):
        if limit is not None:
            exact_ticks = min(exact_ticks, limit)
        self.requested_ticks += exact_ticks
        ticks = rounded(exact_ticks + self.residual)
        if limit is not None:
            ticks = min(ticks, limit)
        self.residual += exact_ticks - ticks
        self.quantisation_ns = max(self.quantisation_ns, abs(self.residual) * 1e9 / self.clock_hz)
        return ticks

    def packet(self, instruction, data=0, ticks=MIN_TICKS):
        if len(self.words) + 2 > self.max_words:
            raise ValueError("compiled program exceeds max_words; use a shorter bounded window")
        self.words.append(instruction | (data << 16))
        self.words.append(ticks - MIN_TICKS)
        self.elapsed += ticks

    def flush(self):
        remaining = self.pending_ticks
        if remaining and remaining < MIN_TICKS:
            raise ValueError("an edge interval is shorter than %d clock ticks" % MIN_TICKS)
        while remaining:
            length = min(remaining, MAX_TICKS)
            if 0 < remaining - length < MIN_TICKS:
                length -= MIN_TICKS - (remaining - length)
            self.packet(OUT_PINS | self.n_pins, self.pending_mask, length)
            remaining -= length
        self.pending_mask, self.pending_ticks = None, 0

    def hold(self, mask, ticks):
        if not ticks:
            return
        if mask != self.pending_mask:
            self.flush()
            self.pending_mask = mask
            rising = mask & ~self.last_mask
            for pin in range(self.n_pins):
                self.pulses[pin] += (rising >> pin) & 1
            self.last_mask = mask
        self.pending_ticks += ticks

    def event(self, event):
        if len(self.events) >= 65535:
            raise ValueError("too many effect events")
        self.events.append(event)
        self.packet(MOV_ISR_OSR, len(self.events), CONTROL_TICKS)
        self.packet(PUSH_BLOCK, ticks=CONTROL_TICKS)

    def print_effect(self, text):
        self.flush()
        self.annotations.append({'kind': 'print', 'text': text, 'nominal_tick': self.elapsed})
        self.event({'kind': 'print', 'text': text})
        self.control_ticks += 2 * CONTROL_TICKS

    def gate(self, trigger, idle_mask):
        before = self.elapsed + self.pending_ticks
        if idle_mask != self.last_mask:
            self.hold(idle_mask, MIN_TICKS)
        self.flush()
        index = len(self.gates)
        if index >= 32767:
            raise ValueError("too many gates")
        entry = trigger.to_dict()
        entry["nominal_tick"] = self.elapsed
        self.gates.append(entry)
        self.event({'kind': 'gate_enter', 'gate': index})
        for level in trigger.levels():
            self.packet(WAIT_GPIO | (level << 7) | trigger.pin, ticks=CONTROL_TICKS)
        self.event({'kind': 'gate_exit', 'gate': index})
        entry["resume_tick"] = self.elapsed
        self.control_ticks += self.elapsed - before

    def finish(self, names, parameters, windowed):
        tail = 0
        if 0 < self.pending_ticks < MIN_TICKS and self.pending_mask == self.final_mask:
            # No edge is needed at the nominal end: fold a short final hold into
            # the final-mask packet, preserving its starting edge and end time.
            tail = self.pending_ticks
            self.pending_mask, self.pending_ticks = None, 0
        self.flush()
        duration = self.elapsed + tail
        self.packet(OUT_PINS | self.n_pins, self.final_mask, MIN_TICKS + tail)
        self.packet(IRQ_DONE)
        return CompiledProgram(self.words, self.clock_hz, self.n_pins, names,
                               duration, self.gates, self.pulses, self.final_mask,
                               self.quantisation_ns, parameters, windowed,
                               self.requested_ticks, self.control_ticks, self.events, self.annotations)


def _carrier_events(command, duration, offset, pin, clock_hz):
    frequency = number(command.freq, "frequency", strict=True)
    end = frequency if command.end_freq is None else number(command.end_freq, "end frequency", strict=True)
    duty = number(command.duty, "duty", strict=True)
    phase = number(command.phase, "phase")
    dead_time = number(command.dead_time_ns, "dead time") / 1e9
    if not isinstance(command.complement, bool) or not duty < 1 or not phase < 1:
        raise ValueError("duty must be in (0, 1), phase in [0, 1), and complement a bool")
    shortest = min(duty, 1 - duty) / max(frequency, end) - dead_time
    if shortest <= 0:
        raise ValueError("dead time leaves no active pulse")
    if shortest * clock_hz < MIN_TICKS or (dead_time and dead_time * clock_hz < MIN_TICKS):
        raise ValueError("an edge interval is shorter than %d clock ticks" % MIN_TICKS)
    sweep = None if command.end_freq is None else number(command.sweep_ms, 'sweep_ms', strict=True) / 1000
    cycles_per_leg = None if sweep is None else (frequency + end) * sweep / 2
    def phase_at(time):
        if sweep is None or frequency == end:
            return frequency * time
        leg = int(floor(time / sweep))
        local = time - leg * sweep
        start = end if leg % 2 else frequency
        slope = (frequency - end if leg % 2 else end - frequency) / sweep
        return leg * cycles_per_leg + start * local + .5 * slope * local * local
    total_cycles = phase_at(duration)
    def at(cycles):
        if cycles >= total_cycles:
            return duration
        if sweep is None or frequency == end:
            return cycles / frequency
        leg = int(floor(cycles / cycles_per_leg))
        local = cycles - leg * cycles_per_leg
        start = end if leg % 2 else frequency
        slope = (frequency - end if leg % 2 else end - frequency) / sweep
        return leg * sweep + 2 * local / (start + sqrt(max(0, start * start + 2 * slope * local)))
    cycle = max(0, int(floor(phase_at(offset) - phase)) - 1)
    while True:
        rise = at(cycle + phase)
        if rise >= duration:
            return
        fall = at(cycle + phase + duty)
        if command.complement:
            if fall + dead_time < duration:
                yield fall + dead_time - offset, pin, 1
            next_rise = at(cycle + phase + 1)
            if next_rise < duration:
                yield next_rise - offset, pin, 0
        else:
            if rise + dead_time < duration:
                yield rise + dead_time - offset, pin, 1
            if fall < duration:
                yield fall - offset, pin, 0
        cycle += 1


def _parallel(writer, signals, length, seconds_per_unit, frequency, remaining):
    import heapq
    total_seconds = length * seconds_per_unit
    start_residual = writer.residual
    total = writer.duration(total_seconds * writer.clock_hz, remaining)
    heap, iterators, mask = [], [], 0
    for pin, signal in enumerate(signals):
        command = signal.command
        if isinstance(command, int):
            integer(command, "channel state", 0, 3)
            if command in (0, 3):
                mask |= (command & 1) << pin
                iterators.append(None)
                continue
            command = _Carrier(frequency, .5, .5 if command == 2 else 0, False, 0)
        elif not isinstance(command, _Carrier):
            raise ValueError("invalid parallel signal command")
        events = _carrier_events(command, signal.duration * seconds_per_unit,
                                 signal.offset * seconds_per_unit, pin, writer.clock_hz)
        iterators.append(events)
        for time, channel, level in events:
            if time < 0:
                mask = (mask & ~(1 << channel)) | (level << channel)
            else:
                heapq.heappush(heap, (time, pin, level))
                break
    previous = 0
    while heap:
        time, pin, level = heapq.heappop(heap)
        tick = rounded(time * writer.clock_hz + start_residual)
        if tick >= total:
            break
        writer.quantisation_ns = max(writer.quantisation_ns,
            abs(tick - time * writer.clock_hz - start_residual) * 1e9 / writer.clock_hz)
        if tick > previous:
            writer.hold(mask, tick - previous)
            previous = tick
        mask = (mask & ~(1 << pin)) | (level << pin)
        try:
            next_time, _, next_level = next(iterators[pin])
            heapq.heappush(heap, (next_time, pin, next_level))
        except StopIteration:
            pass
    writer.hold(mask, total - previous)
    return total


class _WindowDone(BaseException):
    pass


class _Compiler:
    def __init__(self, writer, remaining, frequency, use_ms):
        self.writer, self.remaining = writer, remaining
        self.frequency, self.use_ms = frequency, use_ms

    def consume(self, ticks):
        if self.remaining is not None:
            self.remaining -= ticks

    def effect(self, kind, value):
        writer = self.writer
        if self.remaining is not None and self.remaining <= 0:
            raise _WindowDone()
        before = writer.elapsed + writer.pending_ticks
        needed = 2 * CONTROL_TICKS if kind == 'print' else ((4 + len(value.levels())) * CONTROL_TICKS if kind == 'wait' else 0)
        if self.remaining is not None and self.remaining < needed:
            raise ValueError('bounded window ends inside control overhead')
        if kind == 'sleep':
            ticks = writer.duration(value * writer.clock_hz, self.remaining)
            writer.annotations.append({'kind': 'sleep', 'nominal_tick': before, 'duration_ticks': ticks})
            writer.hold(writer.last_mask, ticks)
        elif kind == 'print':
            writer.print_effect(value)
        elif kind == 'wait':
            writer.gate(value, writer.last_mask)
        else:
            raise ValueError("unknown CtrlAer effect")
        self.consume(writer.elapsed + writer.pending_ticks - before)
        if self.remaining is not None and self.remaining <= 0:
            raise _WindowDone()

    def segment(self, state, length):
        writer, frequency = self.writer, self.frequency
        if not isinstance(state, (_Carrier, _Parallel)):
            integer(state, "state", 0, (1 << (2 * writer.n_pins)) - 1)
        number(length, "duration")
        if length == 0:
            return
        if isinstance(state, (_Carrier, _Parallel)):
            if isinstance(state, _Carrier):
                if writer.n_pins != 1:
                    raise ValueError("combine per-channel carrier commands with mux()")
                signals = (_Signal(state, length),)
            else:
                signals = state.signals
                if len(signals) != writer.n_pins:
                    raise ValueError("mux channel count does not match controller")
            if not self.use_ms:
                integer(length, "ticks", 1, 0x100000000)
            ticks = _parallel(writer, signals, length, .001 if self.use_ms else 1 / frequency,
                              frequency, self.remaining)
            self.consume(ticks)
            return
        count = int(length * frequency / 1000) if self.use_ms else integer(length, "ticks", 1, 0x100000000)
        if count < 1:
            raise ValueError("nonzero duration is shorter than one carrier tick")
        total = rounded(count * writer.clock_hz / frequency)
        if self.remaining is not None:
            total = min(total, self.remaining)
        requested = length * writer.clock_hz / 1000 if self.use_ms else count * writer.clock_hz / frequency
        writer.requested_ticks += min(requested, self.remaining) if self.remaining is not None else requested
        first = state & ((1 << writer.n_pins) - 1)
        second = state >> writer.n_pins
        if first == second:
            writer.hold(first, total)
        else:
            previous = 0
            for half in range(2 * count):
                end = min(total, rounded((half + 1) * writer.clock_hz / (2 * frequency)))
                writer.hold(first if half % 2 == 0 else second, end - previous)
                previous = end
                if end == total:
                    break
        self.consume(total)


def compile_program(program, n_pins=1, freq=108050, clock_hz=None,
                    use_ms=True, window_ms=None, max_segments=100000,
                    max_words=32768, final_mask=0, parameters=None, trigger=None, names=None):
    """Analyse a generator/factory and compile its commands for DMA playback.

    CtrlAer sleep/print/wait effects are interpreted here. Other Python calls and
    global mutations execute normally. A factory receives parameters as keyword
    arguments; an already-created iterator is simply consumed.
    """
    import gc
    threshold = gc.threshold() if hasattr(gc, "threshold") else None
    if threshold is not None:
        # Edge integration creates short-lived floats/tuples. Collect before
        # those fragment the small RP2040 heap around a growing DMA array.
        gc.collect()
        gc.threshold(32768 if threshold < 0 else min(threshold, 32768))
    try:
        return _compile_program(program, n_pins, freq, clock_hz, use_ms, window_ms,
                                max_segments, max_words, final_mask, parameters, trigger, names)
    finally:
        if threshold is not None:
            gc.threshold(threshold)


def _compile_program(program, n_pins, freq, clock_hz, use_ms, window_ms,
                     max_segments, max_words, final_mask, parameters, trigger, names):
    if clock_hz is None:
        try:
            from machine import freq as system_frequency
        except ImportError:
            clock_hz = 125000000
        else:
            clock_hz = system_frequency()
    integer(clock_hz, "clock_hz", 1, 1000000000)
    integer(n_pins, "n_pins", 1, 16)
    integer(max_words, "max_words", 4, 1000000000)
    integer(max_segments, "max_segments", 1, 1000000000)
    integer(final_mask, "final_mask", 0, (1 << n_pins) - 1)
    frequency = number(freq, "frequency", strict=True)
    names = ["CH%d" % i for i in range(n_pins)] if names is None else list(names)
    if len(names) != n_pins or any(not isinstance(name, str) for name in names):
        raise ValueError("names must contain one string per channel")
    parameters = dict(parameters or {})
    window = None if window_ms is None else rounded(number(window_ms, "window_ms") * clock_hz / 1000)
    writer = _Writer(clock_hz, n_pins, max_words, final_mask)
    compiler = _Compiler(writer, window, frequency, use_ms)
    if trigger is not None:
        if not isinstance(trigger, Trigger):
            raise ValueError("trigger must be a Trigger")
        if window is None or window > 0:
            if window is not None and window < (4 + len(trigger.levels())) * CONTROL_TICKS:
                raise ValueError('bounded window ends inside trigger overhead')
            before = writer.elapsed
            writer.gate(trigger, 0)
            compiler.consume(writer.elapsed - before)
    try:
        if callable(program):
            program = _invoke(program, parameters, compiler.effect)
        iterator = iter(program)
        index = 0
        while compiler.remaining is None or compiler.remaining > 0:
            try:
                command = _advance(iterator, compiler.effect)
            except StopIteration:
                break
            if index >= max_segments:
                raise ValueError("program exceeds max_segments; provide a bounded window")
            index += 1
            if compiler.remaining is not None and compiler.remaining <= 0:
                break
            state, length = command
            compiler.segment(state, length)
    except _WindowDone:
        pass
    if window is not None:
        writer.requested_ticks = min(writer.requested_ticks, max(0, window - writer.control_ticks))
    return writer.finish(names, parameters, window_ms is not None)
