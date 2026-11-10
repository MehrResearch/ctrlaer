"""Private bounded, resumable compilation for live generator execution.

This module is portable so chunk continuity can be checked without a board.
It never owns DMA memory after ``fill`` returns. A chunk's final blocking IRQ is
changed to NOP only when the runtime has installed its successor.
"""
from array import array
from ctrlaer_commands import _Carrier, _Parallel, _advance, _invoke
from ctrlaer_program import (MIN_TICKS, CONTROL_TICKS, OUT_PINS, WAIT_GPIO,
                             MOV_ISR_OSR, PUSH_BLOCK, IRQ_DONE, Trigger,
                             integer, number, rounded, _analytic_carrier_events)
from ctrlaer_native import (CarrierEdges, words, _stream_pack, _pulse_pack,
                            _pulse_edges, _lut_edges, _lut_pack, _cached_pack)

IRQ_GUARD = 0xc034  # irq(block,rel(4)): separate from each SM's completion IRQ.
NOP = 0xa042
MAX_CHUNK_EVENTS = 8


class Chunk:
    """Bounded metadata for one DMA block; playback time excludes external waits."""
    def __init__(self, count, ticks, final, events, gates, last_mask):
        self.update(count, ticks, final, events, gates, last_mask)

    def update(self, count, ticks, final, events, gates, last_mask):
        self.count, self.ticks, self.final = count, ticks, final
        self.events, self.gates, self.last_mask = events, gates, last_mask
        return self


class StreamCompiler:
    """Consume a generator incrementally, retaining a command across fills.

    ``buffer_words`` is the capacity of each runtime waveform buffer.
    ``horizon_ms`` is the target playback time of a chunk. A constant level
    can last longer: its complete duration still needs only one packet.
    Very dense control streams instead stop at the packet capacity. Arbitrary
    Python side effects execute during lookahead; managed effects become packets.
    """
    def __init__(self, program, n_pins=1, freq=108050, clock_hz=125000000,
                 use_ms=True, final_mask=0, trigger=None, parameters=None,
                 buffer_words=4096, horizon_ms=20, max_pending_effects=64,
                 _keep_level=False, _initial_mask=0, _scratch=None):
        self.n_pins = integer(n_pins, 'n_pins', 1, 16)
        self.frequency = number(freq, 'frequency', strict=True)
        self._frequency_fp = round(freq * 65536)
        self.clock_hz = integer(clock_hz, 'clock_hz', 1, 1000000000)
        self.final_mask = integer(final_mask, 'final_mask', 0, (1 << n_pins) - 1)
        self.capacity = integer(buffer_words, 'buffer_words', 32, 65536)
        if self.capacity % 2:
            raise ValueError('buffer_words must be even')
        self.horizon = rounded(number(horizon_ms, 'horizon_ms', strict=True) * clock_hz / 1000)
        if self.horizon < self.capacity * MIN_TICKS:
            raise ValueError('horizon_ms is too short for the selected buffer capacity')
        if self.horizon > 0x10000000:
            raise ValueError('horizon_ms exceeds the native chunk coordinate range')
        self.max_pending = integer(max_pending_effects, 'max_pending_effects', 1, 1024)
        self.use_ms, self.parameters = use_ms, dict(parameters or {})
        self.pending, self.iterator = [], None
        self.factory = program
        self.eof = self.finished = False
        self.elapsed = self.command_time = self.command_end = 0
        self.mask = integer(_initial_mask, 'initial mask', 0, (1 << n_pins) - 1)
        self._keep_level = _keep_level
        self.residual = 0
        self.current = self.cursor = self.reference = None
        self.command_index = 0
        self._level_duration = None
        self._level_count = self._level_ticks = 0
        self._cached_key = self._cached_cursor = self._cached_state = None
        self.edge_capacity = 2048
        if _scratch is None:
            self._edge_storage = self.edge_buffer = None
            self.pack_state = words(12)
        else:
            self._edge_storage, self.edge_buffer, self.pack_state = _scratch
        self.edge_index = self.edge_count = self.parity = self.batch_origin = 0
        self.first = self.second = 0
        self.marker = self.gate_index = 0
        if trigger is not None:
            if not isinstance(trigger, Trigger):
                raise ValueError('trigger must be a Trigger')
            self.pending.append(('wait', trigger))

    def _allocate_edges(self):
        # Fixed-frequency carriers pack directly and digital holds need no
        # edge scratch at all. Allocate this only for an actual edge batch.
        try:
            from uctypes import struct, addressof, ARRAY, UINT32
        except ImportError:
            self.edge_buffer = words(self.edge_capacity)
        else:
            # array('I', bytearray(...)) temporarily needs twice the final
            # scratch size. Retain the owner alongside this zero-copy u32 view.
            self._edge_storage = bytearray(self.edge_capacity * 4)
            self.edge_buffer = struct(addressof(self._edge_storage),
                                      {'words':(ARRAY, UINT32 | self.edge_capacity)}).words

    def _effect(self, kind, value):
        if len(self.pending) >= self.max_pending:
            raise ValueError('generator must yield regularly between managed effects')
        self.pending.append((kind, value))

    def _next(self):
        if self.pending:
            return self.pending.pop(0)
        if self.eof:
            return None
        if self.iterator is None:
            source = _invoke(self.factory, self.parameters, self._effect) if callable(self.factory) else self.factory
            self.iterator = iter(source)
            self.factory = None
        try:
            command = _advance(self.iterator, self._effect)
        except StopIteration:
            self.eof = True
        else:
            self.pending.append(('segment', command))
        return self.pending.pop(0) if self.pending else None

    def _start(self, kind, value):
        self.command_index += 1
        if kind == 'sleep':
            ticks = self._ticks(number(value, 'sleep'), 1)
            self.current, self.command_time, self.command_end = 'hold', 0, ticks
            return
        state, length = value
        number(length, 'duration')
        self.command_time = 0
        self.cursor = self.reference = None
        self.edge_index = self.edge_count = self.parity = 0
        if isinstance(state, _Carrier):
            if self.n_pins != 1:
                raise ValueError('combine per-channel carrier commands with mux()')
            units = .001 if self.use_ms else 1 / self.frequency
            if not self.use_ms and length:
                integer(length, 'ticks', 1, 0x100000000)
            seconds = length * units
            self.command_end = self._ticks(length, 1000) if self.use_ms else self._ticks(length * 65536, self._frequency_fp)
            # Validation runs before native code receives parameters.
            reference = _analytic_carrier_events(state, seconds, 0, 0, self.clock_hz)
            first = next(reference, None)
            key = (state.freq, state.end_freq, state.sweep_ms, state.duty,
                   state.phase, state.complement, state.dead_time_ns)
            if key == self._cached_key and self._cached_cursor is not None:
                self.cursor = self._cached_cursor
                if self.cursor._packed_cache:
                    self.cursor.state[0],self.cursor.state[10],self.cursor.state[13] = 0,0,self.cursor._cache[2]
                else:
                    for index in range(len(self._cached_state)):
                        self.cursor.state[index] = self._cached_state[index]
                self.cursor.origin = self.cursor._cache_position = self.cursor._cache_tick = 0
            else:
                self.cursor = CarrierEdges.create(state, self.clock_hz)
                self._cached_key,self._cached_cursor = key,self.cursor
                self._cached_state = array('I',self.cursor.state) if self.cursor is not None else None
            self.first, self.second, self.mask = 1, 0, 0
            if self.cursor is None:
                self.reference = self._reference_edges(first, reference)
            self.current = 'carrier'
        elif isinstance(state, _Parallel):
            self._start_parallel(state, length)
        else:
            integer(state, 'state', 0, (1 << (2 * self.n_pins)) - 1)
            if not self.use_ms:
                integer(length, 'ticks', 0, 0x100000000)
            if length == self._level_duration:
                count, self.command_end = self._level_count, self._level_ticks
            elif self.use_ms:
                if isinstance(length, float) and length * self.frequency / 1000 < 0x1000000:
                    # Match the original/finite conversion for short float
                    # durations. Treating the binary float as an exact rational
                    # floors 0.01 ms at 100 kHz to zero, and 0.08 ms to 7 cycles.
                    count = int(length * self.frequency / 1000)
                else:
                    numerator, scale = self._rational(length)
                    count = numerator * self._frequency_fp // (scale * 1000 * 65536)
            else:
                count = length
            if length and count < 1:
                raise ValueError('nonzero duration is shorter than one carrier tick')
            if length != self._level_duration:
                whole, remainder = divmod(count * self.clock_hz * 65536, self._frequency_fp)
                self.command_end = whole + rounded(remainder / self._frequency_fp)
                self._level_duration = length
                self._level_count, self._level_ticks = count, self.command_end
            self.first = state & ((1 << self.n_pins) - 1)
            self.second = state >> self.n_pins
            self.mask = self.first
            if self.first == self.second:
                self.current = 'hold'
            else:
                command = _Carrier(self.frequency, .5, 0, False, 0)
                self.cursor = CarrierEdges.create(command, self.clock_hz)
                if self.cursor is None:
                    reference = _analytic_carrier_events(command, count / self.frequency, 0, 0, self.clock_hz)
                    self.reference = self._reference_edges(next(reference, None), reference)
                self.current = 'carrier'

    def _reference_edges(self, first, rest):
        if first is not None:
            yield rounded(first[0] * self.clock_hz)
        for event in rest:
            yield rounded(event[0] * self.clock_hz)

    def _rational(self, value):
        if isinstance(value, int):
            return value, 1
        whole = int(value)
        if value == whole:
            return whole, 1
        # Scaling by a power of two preserves MicroPython's float significand.
        # Divide integers before returning to float: a large tick count must
        # never pass through the device's single-precision float representation.
        scale = 1 << 40
        return int(value * scale), scale

    def _ticks(self, value, denominator):
        numerator, scale = self._rational(value)
        whole, remainder = divmod(numerator * self.clock_hz, denominator * scale)
        fraction = remainder / (denominator * scale) + self.residual
        carry = rounded(fraction)
        self.residual = fraction - carry
        return whole + carry

    def _start_parallel(self, state, length):
        # The general merge is deliberately separate from the fast single-edge
        # cursor. It remains resumable and bounded, including unequal ON lengths.
        import heapq
        if len(state.signals) != self.n_pins:
            raise ValueError('mux channel count does not match controller')
        units = .001 if self.use_ms else 1 / self.frequency
        residual = self.residual
        self.command_end = self._ticks(length, 1000) if self.use_ms else self._ticks(length * 65536, self._frequency_fp)
        self.mask = 0
        heap, sources = [], []
        for pin, signal in enumerate(state.signals):
            command = signal.command
            if isinstance(command, int):
                integer(command, 'channel state', 0, 3)
                if command in (0, 3):
                    self.mask |= (command & 1) << pin
                    sources.append(None)
                    continue
                command = _Carrier(self.frequency, .5, .5 if command == 2 else 0, False, 0)
            events = _analytic_carrier_events(command, signal.duration * units,
                                              signal.offset * units, pin, self.clock_hz)
            sources.append(events)
            for time, _, level in events:
                tick = rounded(time * self.clock_hz + residual)
                if time < 0:
                    self.mask = self.mask & ~(1 << pin) | level << pin
                else:
                    heapq.heappush(heap, (tick, pin, level))
                    break

        def merged():
            mask = self.mask
            while heap:
                tick = heap[0][0]
                while heap and heap[0][0] == tick:
                    _, pin, level = heapq.heappop(heap)
                    mask = mask & ~(1 << pin) | level << pin
                    event = next(sources[pin], None)
                    if event is not None:
                        heapq.heappush(heap, (rounded(event[0] * self.clock_hz + residual), pin, event[2]))
                yield tick, mask
        self.reference = merged()
        self.current = 'parallel'
        self._parallel_next = next(self.reference, None)

    def _packet(self, instruction, data, ticks):
        if ticks < MIN_TICKS:
            raise ValueError('an edge interval is shorter than 6 clock ticks')
        self.output[self.used] = instruction | data << 16
        self.output[self.used + 1] = ticks - MIN_TICKS
        self.used += 2
        self.elapsed += ticks

    def _event(self, event):
        if not self.events:
            self.events = []
        self.marker = self.marker % 65535 + 1
        self.events.append((self.marker, event))
        self._packet(MOV_ISR_OSR, self.marker, CONTROL_TICKS)
        self._packet(PUSH_BLOCK, 0, CONTROL_TICKS)

    def _control(self, kind, value):
        if kind == 'print':
            self._event(('print', value))
        else:
            gate = self.gate_index
            self.gate_index += 1
            # Keep private streaming metadata compact. Expanding each wait into
            # several dictionaries consumed the RP2040 heap during short shots,
            # despite the bounded waveform pool. Public histories remain dicts.
            entry = ('gate_enter', gate, value, self.elapsed)
            if not self.gates:
                self.gates = []
            self.gates.append(entry)
            self._event(entry)
            for level in value.levels():
                self._packet(WAIT_GPIO | level << 7 | value.pin, 0, CONTROL_TICKS)
            self._event(('gate_exit', gate))

    def _carrier(self, deadline, limit):
        if self.cursor is not None and self.cursor._period and not self.cursor._packed_cache:
            if self.edge_buffer is None:
                self._allocate_edges()
            self.cursor.fill_into(self.edge_buffer,self.edge_capacity)
            cache = self.cursor._cache
            state = self.cursor.state
            state[0],state[1] = 0,self.cursor._period_edges
            state[2],state[3] = state[1]//2 if cache[4] else state[1],cache[3]
            state[10],state[13] = 0,cache[2]
            self.cursor.origin = 0
            self.cursor._packed_cache = True
        direct = self.cursor is not None and (self.cursor.kernel is _pulse_edges or
                                             self.cursor.kernel is _lut_edges or
                                             self.cursor._packed_cache)
        if direct:
            if self.cursor.state[13] >= 0x20000000:
                shift = self.cursor.state[13]
                self.cursor.origin += shift
                self.cursor.state[13] = 0
            self.batch_origin = self.cursor.origin
        elif self.edge_index + 1 == self.edge_count and self.edge_buffer[self.edge_index] + self.batch_origin == self.command_time:
            self.mask = self.first if self.parity == 0 else self.second
            self.parity = 1 - self.parity
            self.edge_index += 1
        if not direct and self.edge_index == self.edge_count:
            if self.edge_buffer is None:
                self._allocate_edges()
            if self.cursor is not None:
                self.cursor.fill_into(self.edge_buffer, self.edge_capacity)
                self.batch_origin = self.cursor.origin
                self.edge_count = self.edge_capacity
            else:
                self.batch_origin = self.command_time
                self.edge_count = 0
                for index in range(self.edge_capacity):
                    tick = next(self.reference, None)
                    if tick is None:
                        tick = self.command_end
                    self.edge_buffer[index] = tick - self.batch_origin
                    self.edge_count += 1
                    if tick == self.command_end:
                        break
            self.edge_index = 0
        stop = min(self.command_end, self.command_time + deadline - self.elapsed)
        p = self.pack_state
        p[0], p[1] = self.edge_index, self.edge_count
        local = self.command_time - self.batch_origin
        p[2] = local if local >= 0 else local + 0x100000000
        p[3] = stop - self.batch_origin
        p[4], p[5], p[6] = self.mask, self.first, self.second
        p[7], p[8], p[9] = self.parity, self.used, limit
        p[10], p[11] = OUT_PINS | self.n_pins, int(stop < self.command_end)
        before = self.command_time
        if direct and self.cursor._packed_cache:
            written = _cached_pack(self.cursor.state,self.cursor._cache[0],self.output,p)
        elif direct and self.cursor.kernel is _lut_edges:
            written = _lut_pack(self.cursor.state,self.cursor._lut[1],self.output,p)
        elif direct:
            written = _pulse_pack(self.cursor.state,self.output,p)
        else:
            written = _stream_pack(self.edge_buffer,self.output,p)
        if written < 0:
            raise ValueError('an edge interval is shorter than 6 clock ticks')
        self.edge_index, self.mask, self.parity, self.used = p[0], p[4], p[7], p[8]
        local = p[2] if p[2] < 0x80000000 else p[2] - 0x100000000
        self.command_time = local + self.batch_origin
        self.elapsed += self.command_time - before

    def fill(self, output, count=None, reuse=None):
        """Fill one reusable u32 array and return its bounded metadata.

        The guard occupies six ticks taken from the final packet's hold. DMA uses
        the actual count, so sparse blocks need no padding. No final mask is
        applied at an internal boundary.
        """
        if self.finished:
            return None
        if (len(output) if count is None else count) != self.capacity:
            raise ValueError('output buffer does not match buffer_words')
        self.output, self.used, self.events, self.gates = output, 0, (), ()
        start = self.elapsed
        deadline = start + self.horizon
        advances = 0
        while True:
            if self.used:
                can_guard = output[self.used-1] >= MIN_TICKS
                if (self.elapsed >= deadline or self.used >= self.capacity - 16 or
                        len(self.events) >= MAX_CHUNK_EVENTS) and can_guard:
                    output[self.used-1] -= MIN_TICKS
                    output[self.used], output[self.used+1] = IRQ_GUARD, 0
                    count = self.used + 2
                    self.output = None
                    if reuse is None:
                        return Chunk(count, self.elapsed-start, False, self.events, self.gates, self.mask)
                    return reuse.update(count, self.elapsed-start, False, self.events, self.gates, self.mask)
            if self.used >= self.capacity - 2:
                raise ValueError('no room for a time-neutral chunk guard at this edge rate')
            if self.current is None:
                if self.used > self.capacity - 16:
                    raise ValueError('control boundary needs a longer preceding hold')
                item = self._next()
                advances += 1
                if advances > 100000:
                    raise ValueError('generator must yield positive durations or managed effects regularly')
                if item is None:
                    end_mask = self.mask if self._keep_level else self.final_mask
                    self._packet(OUT_PINS | self.n_pins, end_mask, MIN_TICKS)
                    self._packet(IRQ_DONE, 0, MIN_TICKS)
                    self.mask, self.finished, self.output = end_mask, True, None
                    if reuse is None:
                        return Chunk(self.used, self.elapsed-start, True, self.events, self.gates, self.mask)
                    return reuse.update(self.used, self.elapsed-start, True, self.events, self.gates, self.mask)
                kind, value = item
                if kind in ('print', 'wait'):
                    self._control(kind, value)
                    continue
                previous_mask = self.mask
                self._start(kind, value)
                if not self.command_end:
                    self.mask = previous_mask
                    self.current = None
                    continue
            remaining = self.command_end - self.command_time
            if self.current == 'carrier':
                # Leave room for an entire gate protocol or the final tail.
                self._carrier(max(deadline, self.elapsed + MIN_TICKS * self.capacity), self.capacity - 2)
            else:
                if self.current == 'parallel':
                    while self._parallel_next is not None and self._parallel_next[0] <= self.command_time:
                        self.mask = self._parallel_next[1]
                        self._parallel_next = next(self.reference, None)
                    if self._parallel_next is not None:
                        remaining = min(remaining, self._parallel_next[0] - self.command_time)
                # A long constant level costs one packet, regardless of duration.
                # Splitting an 800 ms hold into 20 ms chunks creates forty
                # unnecessary refill deadlines and little useful lookahead.
                # Keep the hold intact; only the hardware counter limits it.
                length = min(remaining, 0xffffffff)
                self._packet(OUT_PINS | self.n_pins, self.mask, length)
                self.command_time += length
            if self.command_time >= self.command_end:
                self.current = None
