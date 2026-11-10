"""Private RP2 chained-DMA runtime for bounded-memory generator playback."""
from array import array
from uctypes import addressof, struct, ARRAY, UINT32
from machine import Pin, freq, mem32, disable_irq, enable_irq
import micropython
from rp2 import DMA, StateMachine
from time import ticks_us, ticks_ms, ticks_diff
from gc import isenabled as gc_enabled, collect as gc_collect

from ctrlaer_program import integer
from ctrlaer_stream import StreamCompiler, NOP
from ctrlaer_runtime import (PreparedRun, _engine, _engine_users, claim, pio_base,
                             UnderrunError, GateTimeoutError, _print)

DMA_INTR = 0x50000400


@micropython.viper
def _publish_dma(s: ptr32, completed: int, timestamp: int):
    """Allocation-free hard-IRQ handoff of one fully prepared spare block.

    Header: pending mask, next DMA, ready slot+1, free write/read indices,
    error, terminal queued, publication count. TX slots start at 9; four DMA
    descriptors at 16 (base/normal/final, stride 5), five block descriptors at
    40 (address/count/final/guard, stride 4), free-slot ring at 64.
    """
    if completed >= 0:
        s[0] = s[0] | uint(1 << completed)
        s[16 + completed * 5 + 3] = uint(timestamp)
    if int(s[5]) or int(s[6]) or not int(s[2]):
        return
    index = int(s[1]); bit = uint(1 << index)
    if not s[0] & bit:
        return
    last = (index + 3) & 3
    if s[0] & uint(1 << last):
        s[5] = uint(1)
        return
    slot = int(s[2]) - 1
    old_slot = s[9 + index]
    dma = 16 + index * 5
    block = 40 + slot * 4
    registers = ptr32(s[dma])
    registers[0] = s[block]
    registers[2] = s[block + 1]
    registers[4] = s[dma + (2 if s[block + 2] else 1)]  # AL1_CTRL: no trigger.
    previous = int(s[9 + last])
    guard = ptr32(s[40 + previous * 4 + 3])
    guard[0] = uint(0xa042)
    s[9 + index] = uint(slot)
    s[0] = s[0] & ~bit
    s[1] = uint((index + 1) & 3)
    s[2] = uint(0)
    s[6] = s[block + 2]
    position = int(s[3])
    next_position = (position + 1) & 7
    if next_position == int(s[4]):
        s[5] = uint(2)
        return
    s[64 + position] = old_slot
    s[3] = uint(next_position)
    s[7] = s[7] + uint(1)
    s[8] = s[dma + 3]


class _Aligned:
    def __init__(self, count):
        self.count = count
        self.storage = bytearray(count * 8)
        size = count * 4
        offset = (-addressof(self.storage)) % size
        self.words = struct(addressof(self.storage)+offset,
                            {'words':(ARRAY, UINT32 | count)}).words
        self.ring_bits = 0
        while 1 << self.ring_bits < size:
            self.ring_bits += 1


class _Block:
    pass


def _pool(count, capacity):
    """Share alignment slack between blocks instead of wasting half each one."""
    # A typed uctypes view avoids a second full-size array copy while allocating
    # the pool. On a Tiny that temporary copy can exhaust the contiguous heap.
    owner = bytearray((count + 1) * capacity * 4)
    offset = (-addressof(owner)) % (capacity * 4)
    blocks = []
    bits = 0
    while 1 << bits < capacity * 4:
        bits += 1
    for index in range(count):
        block = _Block()
        block.storage = owner
        address = addressof(owner) + offset + index * capacity * 4
        block.words = struct(address, {'words':(ARRAY, UINT32 | capacity)}).words
        block.ring_bits = bits
        blocks.append(block)
    # The alignment slack is outside every DMA read ring. At the default
    # 16 KiB alignment, one of its two ends always has at least 8 KiB free.
    # Reuse that space for the compiler's edge scratch instead of allocating
    # another large object in the Tiny's non-moving heap.
    scratch = None
    spare = 0 if offset >= 8192 else offset + count * capacity * 4
    available = offset if spare == 0 else len(owner) - spare
    if available >= 8192:
        from ctrlaer_native import words
        view = struct(addressof(owner) + spare, {'words':(ARRAY, UINT32 | 2048)}).words
        scratch = owner, view, words(12)
    # Two 1 KiB receive rings also fit the remaining alignment slack at the
    # default capacity. Keep them disjoint from TX and edge-scratch regions.
    receive = None
    if scratch is not None:
        regions = ([(8192,offset-8192),
                    (offset+count*capacity*4,len(owner)-offset-count*capacity*4)]
                   if spare == 0 else
                   [(0,offset),(spare+8192,len(owner)-spare-8192)])
        for first,length in regions:
            aligned = first+(-(addressof(owner)+first)) % 1024
            if aligned+2048 <= first+length:
                receive = []
                for index in range(2):
                    block = _Block()
                    block.storage = owner
                    block.words = struct(addressof(owner)+aligned+index*1024,
                                         {'words':(ARRAY, UINT32 | 256)}).words
                    block.ring_bits = 10
                    block.count = 256
                    receive.append(block)
                break
    return blocks, scratch, receive


class _Header:
    def __init__(self, compiler):
        self.clock_hz, self.n_pins = compiler.clock_hz, compiler.n_pins
        self.final_mask = compiler.final_mask
        self.gates = []


class BufferedRun(PreparedRun):
    """Four chained TX DMAs and one spare block with fail-closed chunk guards.

    Generator work runs in the MicroPython soft service context during lookahead.
    All output edges and GPIO waits are PIO instructions. A late successor leaves
    its predecessor's blocking guard intact, so stale cyclic DMA data cannot be
    played. Software then applies the configured final mask and raises an error.

    Returned by `CtrlAer.prepare(mode='buffered')`; construct through that method.
    `buffer_words` is the capacity of each of five buffers, a power of two from
    32 to 8192 (default 4096). The aligned pool uses six times that capacity in
    bytes per u32: 96 KiB at the default, plus cursor/event/cache storage. The
    default pool's unused alignment space also holds the 8 KiB edge scratch;
    smaller pools allocate scratch separately when a carrier needs it. Four
    TX and two RX DMA channels are claimed. Available heap can limit capacity.

    `history_limit` bounds both observation histories (default 128; 0 disables
    retention). `poll_ms` defaults to 1 ms. Other options go to the incremental
    compiler: `use_ms`, `freq`, `clock_hz`, `n_pins`, `final_mask`, initial
    `trigger`, factory `parameters`, `horizon_ms=20`, `max_pending_effects=64`.
    The horizon is a sparse-hold lookahead target, not a total-duration limit.

    The usual `PreparedRun` lifecycle is shared. `program` is a timing/output
    header, not a complete `CompiledProgram`; no whole-program `words`, preview
    or packet record exists for an unconsumed future. Compile a separate finite
    analysis window when those artifacts are needed. Generator/factory Python
    side effects execute during lookahead; managed operations follow playback.
    """
    def __init__(self, controller, program, buffer_words=4096, poll_ms=1,
                 history_limit=128, **options):
        integer(poll_ms, 'poll_ms', 1, 1000)
        integer(history_limit, 'history_limit', 0, 100000)
        integer(buffer_words, 'buffer_words', 32, 8192)
        if buffer_words & (buffer_words - 1):
            raise ValueError('buffer_words must be a power of two')
        defer = options.pop('_defer', False)
        reuse_pool = options.pop('_reuse_pool', None)
        options.setdefault('clock_hz', freq())
        options.setdefault('n_pins', controller.n_pins)
        options.setdefault('freq', controller._freq)
        options['buffer_words'] = buffer_words
        # Reserve the largest object before small cursor allocations can split
        # the old run's freed pool on the non-moving RP2040 heap.
        import gc
        gc.collect()
        self.buffers, scratch, receive = (_pool(5,buffer_words) if reuse_pool is None
                                          else reuse_pool)
        self.compiler = StreamCompiler(program, _scratch=scratch, **options)
        self._source_factory = program if callable(program) else None
        self._compile_options = options
        self.controller, self.program = controller, _Header(self.compiler)
        if self.program.n_pins != controller.n_pins or self.program.clock_hz != freq():
            raise ValueError('buffered clock and channel count must match the controller')
        self.sm_id, self.base_pin = controller._sm_id, controller.base_pin
        self.pio_index, self.local_sm = self.sm_id // 4, self.sm_id % 4
        self.base, self.bit = pio_base(self.sm_id), 1 << self.local_sm
        self._irq_address, self._debug_address = self.base + 0x30, self.base + 8
        self.guard_bit = self.bit << 4
        self.poll_ms, self.history_limit = poll_ms, history_limit
        self.buffer_words = buffer_words
        self.state, self.error, self.closed = 'preparing', None, False
        self.sm = self._timer = self._tx = self._rx = None
        self._txs, self._rxs = [], []
        self._events = {}
        self._registered = self._servicing = False
        self._group = None
        self.mode = 'buffered'
        self._program = _engine(self.program.n_pins)
        self._key = (self.pio_index, self.program.n_pins)
        self._receive = receive if receive is not None else [_Aligned(256), _Aligned(256)]
        if reuse_pool is not None:
            for block in self._receive:
                for index in range(block.count):
                    block.words[index] = 0
        self.chunks = [None, None, None, None, None]
        claim(self, self.sm_id, self.base_pin, self.program.n_pins)
        try:
            self.sm = StateMachine(self.sm_id, self._program, freq=self.program.clock_hz,
                                   out_base=Pin(self.base_pin))
            _engine_users[self._key] = _engine_users.get(self._key, 0) + 1
            self._registered = True
            if defer:
                # Eager execution already owns the GPIO/PIO but has not seen
                # its first command. Do not configure an empty DMA fragment.
                self.gate_history, self.effect_history = [], []
                self.elapsed_us = self.refill_count = self.max_refill_us = self.max_publish_us = self.max_gc_us = 0
                self._gate = self._gate_since = self._gate_timeout = None
                self.state = 'deferred'
            else:
                self._configure()
        except BaseException:
            self.close()
            raise

    def _stop_dma(self):
        if hasattr(self, '_irq_state'):
            self._irq_state[6] = 1
        if self.sm is not None:
            self.sm.active(0)
        for dma in self._txs + self._rxs:
            dma.active(0)
        for dma in self._txs + self._rxs:
            dma.close()
        self._txs, self._rxs = [], []

    def _dma_irq(self, dma):
        # Every value here is a small integer or existing object. No metadata,
        # generator code, printing or allocation runs in the hard interrupt.
        started = ticks_us()
        _publish_dma(self._irq_state, self._irq_map[dma.channel], started)
        elapsed = ticks_diff(ticks_us(), started)
        if elapsed > self.max_publish_us:
            self.max_publish_us = elapsed

    def _register(self, chunk):
        if not chunk.events:
            return
        for gate in chunk.gates:
            if self.base_pin <= gate['pin'] < self.base_pin + self.program.n_pins:
                raise ValueError("a gate cannot wait on this controller's own output GPIO")
        for marker, event in chunk.events:
            if marker in self._events:
                raise UnderrunError('event lookahead exceeded the marker sequence capacity')
            self._events[marker] = event

    def _configure_tx(self, index):
        dma = self._txs[index]
        slot = self._tx_slots[index]
        chunk = self.chunks[slot]
        if self._tx_controls[index] is None:
            normal = dma.pack_ctrl(size=2, inc_read=True, inc_write=False, high_pri=True,
                                  ring_size=self.buffers[slot].ring_bits, ring_sel=False,
                                  chain_to=self._txs[(index+1)%4].channel,
                                  treq_sel=self.pio_index * 8 + self.local_sm, irq_quiet=False)
            final = dma.pack_ctrl(default=normal, chain_to=dma.channel)
            self._tx_controls[index] = normal, final
            dma.config(write=self.sm, ctrl=final if chunk.final else normal)
        # No per-refill kwargs, control packing or temporary configuration
        # objects in the publication window. These setters do not trigger DMA.
        dma.read = self.buffers[slot].words
        dma.count = chunk.count
        dma.ctrl = self._tx_controls[index][1 if chunk.final else 0]

    def _configure(self, preserve=False, keep_history=False):
        self._stop_dma()
        if preserve:
            self.sm.restart()
            shift = self.base + 0xd0 + self.local_sm * 24
            original = mem32[shift]
            mem32[shift] = original ^ (1 << 31)
            mem32[shift] = original
            self.sm.exec(self._entry_pc & 31)  # Raw unconditional JMP encoding.
        else:
            self.sm.init(self._program, freq=self.program.clock_hz, out_base=Pin(self.base_pin))
            self._entry_pc = mem32[self.base + 0xd4 + self.local_sm * 24]
        self.sm.irq(handler=None)
        mem32[self.base + 0x30] = self.bit | self.guard_bit
        mem32[self.base + 8] = self.bit << 24 | self.bit
        self._events, self._receive_index, self._expected_marker = {}, 0, 1
        self._gate = self._gate_since = self._gate_timeout = None
        if not preserve and not keep_history:
            self.gate_history, self.effect_history = [], []
            self.elapsed_us = self.refill_count = self.max_refill_us = self.max_publish_us = self.max_gc_us = 0
        self._gc_due = self.refill_count + 8
        self._terminal_enqueued = False
        self._tx_slots, self._ready_slot = [0, 1, 2, 3], None
        for slot in range(5):
            self.chunks[slot] = self.compiler.fill(self.buffers[slot].words, self.compiler.capacity)
            self._register(self.chunks[slot])
            if slot and slot < 4:
                self.buffers[slot-1].words[self.chunks[slot-1].count-2] = NOP
            if slot == 4:
                self._ready_slot = slot
            if self.chunks[slot].final:
                break
        self.program.gates = list(self.chunks[0].gates)
        self._gc_command = self.compiler.command_index
        self._gc_due = self.refill_count+self._gc_spacing()
        self._terminal_enqueued = any(chunk is not None and chunk.final for chunk in self.chunks[:4])
        for _ in range(4):
            self._txs.append(DMA())
        self._tx_controls = [None, None, None, None]
        for _ in range(2):
            self._rxs.append(DMA())
        for index in range(2):
            receive = self._rxs[index]
            receive.config(read=self.sm, write=self._receive[index].words, count=256,
                ctrl=receive.pack_ctrl(size=2, inc_read=False, inc_write=True,
                    ring_size=self._receive[index].ring_bits, ring_sel=True,
                    treq_sel=self.pio_index * 8 + 4 + self.local_sm,
                    chain_to=self._rxs[1-index].channel))
        for index in range(4):
            if self.chunks[index] is not None:
                self._configure_tx(index)
        self._tx_mask = sum(1 << dma.channel for dma in self._txs)
        mem32[DMA_INTR] = self._tx_mask
        from ctrlaer_native import words
        self._irq_state = words(72)
        self._irq_map = array('I', [255] * 16)
        state = self._irq_state
        state[2] = 0 if self._ready_slot is None else self._ready_slot + 1
        state[6] = int(self._terminal_enqueued)
        for index in range(4):
            dma = self._txs[index]
            state[9+index] = self._tx_slots[index]
            self._irq_map[dma.channel] = index
            base = 16 + index * 5
            state[base] = 0x50000000 + dma.channel * 64
            # Unused channels after an early EOF have no config and cannot run.
            if self._tx_controls[index] is not None:
                state[base+1], state[base+2] = self._tx_controls[index]
        for slot in range(5):
            base = 40 + slot * 4
            state[base] = addressof(self.buffers[slot].words)
            if self.chunks[slot] is not None:
                state[base+1] = self.chunks[slot].count
                state[base+2] = int(self.chunks[slot].final)
                state[base+3] = state[base] + (self.chunks[slot].count-2) * 4
        for dma in self._txs:
            dma.irq(handler=self._dma_irq, hard=True)
        self._rxs[0].active(1)
        self._txs[0].active(1)
        started = ticks_ms()
        while self.sm.tx_fifo() < min(4, self.chunks[0].count):
            if ticks_diff(ticks_ms(), started) > 100:
                raise RuntimeError('DMA did not prefill the transmit FIFO')
        self.state, self.error = 'prepared', None

    def _history(self, target, item):
        if not self.history_limit:
            return
        target.append(item)
        if len(target) > self.history_limit:
            target.pop(0)

    def _arm_service(self):
        # Drop construction temporaries before the first refill deadline. On
        # the small RP2040 heap a full collection can exceed a chunk's duration.
        import gc
        gc.collect()
        if self.mode == 'eager' and self._eager_running:
            now = ticks_us()
            self.elapsed_us += ticks_diff(now, self._last_us)
        super()._arm_service()

    def _read_gate_events(self):
        while True:
            part, position = divmod(self._receive_index, 256)
            buffer = self._receive[part].words
            marker = buffer[position]
            if not marker:
                break
            if marker != self._expected_marker:
                raise UnderrunError('gate/effect receive ring was overwritten')
            event = self._events.pop(marker, None)
            if event is None:
                raise UnderrunError('gate/effect metadata is missing')
            buffer[position] = 0
            self._receive_index = (self._receive_index + 1) % 512
            self._expected_marker = marker % 65535 + 1
            if event['kind'] == 'print':
                self._history(self.effect_history, {'kind':'print', 'text':event['text'],
                                                   'observed_elapsed_us':self.elapsed_us})
                _print(event['text'], end='')
            else:
                entering = event['kind'] == 'gate_enter'
                self._history(self.gate_history, {'gate':event['gate'], 'entering':entering,
                                                 'observed_elapsed_us':self.elapsed_us})
                if entering:
                    self._gate, self._gate_since = event['gate'], ticks_ms()
                    self._gate_timeout = event['trigger']['timeout_ms']
                    self.state = 'armed' if self._gate == 0 and event['trigger']['nominal_tick'] == 0 else 'waiting'
                else:
                    self._gate = self._gate_since = self._gate_timeout = None
                    self.state = 'running'

    def _gc_spacing(self):
        compiler = self.compiler
        return (32 if compiler.current == 'carrier' and compiler.cursor is not None
                and (compiler.cursor._lut is not None or compiler.cursor._cache is not None)
                else 8)

    def _refill(self):
        state = self._irq_state
        if state[5]:
            raise UnderrunError('DMA handoff missed an unpublished successor')
        if state[6]:
            return
        if state[4] == state[3]:
            return
        begin = ticks_us()
        position = state[4]
        old_slot = state[64 + position]
        state[4] = (position + 1) & 7
        if self.compiler.command_index != self._gc_command:
            self._gc_due = min(self._gc_due,self.refill_count+8)
        if (self.refill_count >= self._gc_due and gc_enabled() and not state[0]
                and ticks_diff(ticks_us(), state[8]) <= 1000):
            collected = ticks_us()
            gc_collect()
            self.max_gc_us = max(self.max_gc_us, ticks_diff(ticks_us(), collected))
            # A burst boundary builds fresh cursor/validation objects. Waiting
            # 32 refills can exhaust the Tiny's remaining heap first, forcing
            # an automatic collection inside production as well as this one.
            self._gc_command = self.compiler.command_index
            self._gc_due = self.refill_count+self._gc_spacing()
        chunk = self.compiler.fill(self.buffers[old_slot].words, self.compiler.capacity,
                                   self.chunks[old_slot])
        if not self.active:
            return  # Producer code may have aborted or closed this run.
        self._register(chunk)
        self.chunks[old_slot] = chunk
        descriptor = 40 + old_slot * 4
        state[descriptor+1], state[descriptor+2] = chunk.count, int(chunk.final)
        state[descriptor+3] = state[descriptor] + (chunk.count-2)*4
        irq_state = disable_irq()
        try:
            state[2] = old_slot + 1
            # A completion may have arrived while this block was being built.
            # Retry its handoff with interrupts masked for this tiny native call.
            _publish_dma(state, -1, 0)
        finally:
            enable_irq(irq_state)
        self.refill_count += 1
        self.max_refill_us = max(self.max_refill_us, ticks_diff(ticks_us(), begin))

    def poll(self):
        """Service DMA handoffs, production, events and timeouts; return state.

        May advance the generator and perform its ordinary Python side effects.
        Edge timing and GPIO waits remain in PIO. Records errors/applies the
        final mask; `wait()` propagates the recorded exception. This is soft
        interpreter work, so a long blocking producer can miss its deadline.
        """
        if not self.active or self._servicing:
            return self.state
        self._servicing = True
        try:
            now = ticks_us()
            self.elapsed_us += ticks_diff(now, self._last_us)
            self._last_us = now
            if self._events:
                self._read_gate_events()
            flags = mem32[self._irq_address]
            if flags & self.bit:
                self._read_gate_events()
                if self._events:
                    return self.state
                self._stop_dma()
                self._stop_timer()
                self.state = 'done'
            elif flags & self.guard_bit:
                self._fail(UnderrunError('PIO reached an unpublished chunk successor'))
            elif self._irq_state[5]:
                self._fail(UnderrunError('DMA handoff missed an unpublished successor'))
            elif freq() != self.program.clock_hz:
                self._fail(RuntimeError('system clock changed during execution'))
            elif mem32[self._debug_address] & (self.bit << 24 | self.bit):
                self._fail(UnderrunError('PIO stalled waiting for DMA data or receive space'))
            else:
                # A collection can leave more than one completion pending.
                # Produce consecutively while catching up, without adding a
                # polling-period sleep between already queued handoffs.
                for _ in range(4):
                    self._refill()
                    if self._events:
                        self._read_gate_events()
                    if self._irq_state[6] or self._irq_state[4] == self._irq_state[3]:
                        break
                if (self._gate is not None and self._gate_timeout is not None and
                        ticks_diff(ticks_ms(), self._gate_since) >= self._gate_timeout):
                    self._fail(GateTimeoutError('gate %d timed out' % self._gate))
        except Exception as error:
            self._fail(error)
        finally:
            self._servicing = False
        return self.state

    def status(self):
        """Return the usual runtime snapshot plus bounded-production statistics.

        Services once first. Additional keys are `mode`, `buffer_words`,
        `buffer_count`, `tx_dma_channels`, `queued_events`, `history_limit`,
        `refill_count`, `max_refill_us`, `max_publish_us`, and `max_gc_us`.
        Maximum refill time describes completed refills, including a scheduled
        collection when present; publish time is the DMA handoff interval.
        GC time measures explicitly scheduled collections, not every automatic
        collection the interpreter may perform. Values are service observations.
        """
        result = super().status()
        result.update(mode=self.mode, refill_count=self.refill_count,
                      max_refill_us=self.max_refill_us, max_publish_us=self.max_publish_us,
                      max_gc_us=self.max_gc_us,
                      buffer_words=self.buffer_words,
                      queued_events=len(self._events), history_limit=self.history_limit,
                      buffer_count=5, tx_dma_channels=4)
        return result

    def rearm(self):
        """Recreate a factory's generator and prepare it again; return this run.

        Requires an inactive open run originally prepared from a callable.
        Already-created iterators cannot be rewound and raise `RuntimeError`.
        Factory code/side effects execute again, histories and counters reset,
        and output initialises LOW before the next `start()`.
        """
        if self.closed or self.active:
            raise RuntimeError('only an inactive open run can be rearmed')
        if self._source_factory is None:
            raise RuntimeError('rearming buffered execution requires a generator factory')
        self._stop_timer()
        scratch = (self.compiler._edge_storage, self.compiler.edge_buffer, self.compiler.pack_state)
        self.compiler = None
        gc_collect()
        self.compiler = StreamCompiler(self._source_factory, _scratch=scratch, **self._compile_options)
        for receive in self._receive:
            for index in range(receive.count):
                receive.words[index] = 0
        self.chunks = [None, None, None, None, None]
        self._configure()
        return self

    def _replace(self, program, initial_mask, effect=None):
        """Reuse hardware for one eager fragment without reinitialising pins."""
        if self.closed or self.active:
            raise RuntimeError('eager fragment requires an inactive open run')
        self._stop_timer()
        options = dict(self._compile_options)
        options.pop('trigger', None)  # An initial trigger belongs to the whole run.
        options.update(_keep_level=True, _initial_mask=initial_mask)
        next_gate = self.compiler.gate_index
        scratch = (self.compiler._edge_storage, self.compiler.edge_buffer, self.compiler.pack_state)
        self.compiler = None
        if self.state != 'deferred':
            gc_collect()
        # A deferred eager run has no previous waveform/cursor to reclaim.
        # start() collects after its first fragment has been prepared.
        self.compiler = StreamCompiler(program, _scratch=scratch, **options)
        self.compiler.gate_index = next_gate
        if effect is not None:
            self.compiler.pending.append(effect)
        self.program = _Header(self.compiler)
        for receive in self._receive:
            for index in range(receive.count):
                receive.words[index] = 0
        self.chunks = [None, None, None, None, None]
        self._configure(preserve=self.state != 'deferred', keep_history=True)

    def abort(self):
        """Stop playback and apply the final mask; return this run.

        In eager execution, cancellation also stops future generator commands,
        including when called between hardware fragments. Already executing
        ordinary Python code runs until it next yields or calls a managed
        operation. An inactive completed run stays completed; closed runs raise
        `RuntimeError`. Hardware remains reserved until `close()`.
        """
        if self.mode == 'eager' and self._eager_running and not self.closed:
            self._eager_cancelled = True
            self._stop_timer()
            self._stop_dma()
            self._drive_final()
            self.state = 'aborted'
            return self
        return super().abort()

    def close(self):
        """Close the run and release its waveform buffers; return `None`.

        Active playback first aborts with its final mask. Idempotent; closed
        runs cannot be rearmed. Status/history remain inspectable, while large
        playback allocations are released even if this object is retained.
        """
        if self.closed:
            return
        if self.mode == 'eager' and self._eager_running:
            self.abort()
        super().close()
        # Closed stream buffers are not replay artifacts. Release their large
        # backing allocations even if the caller retains the closed run object.
        self.buffers, self._receive, self.chunks = [], [], []
        self.compiler = None
        self._events.clear()


def run_eager(controller, program, use_ms=True, **options):
    """Execute each command before resuming its generator, holding GPIO in gaps."""
    from ctrlaer_commands import _advance, _invoke
    options['_keep_level'] = True
    options['_defer'] = options.get('trigger') is None
    run = controller.prepare([], mode='buffered', use_ms=use_ms, **options)
    run.mode = 'eager'
    run._eager_running, run._eager_cancelled = True, False
    run._last_us = ticks_us()
    level = 0

    def execute(source, effect=None):
        nonlocal level
        if run._eager_cancelled:
            return
        run._replace(source, level, effect)
        run.start().wait()
        if run._eager_cancelled:
            return
        for chunk in run.chunks:
            if chunk is not None and chunk.final:
                level = chunk.last_mask
                break

    def handle(kind, value):
        if run._eager_cancelled:
            return
        if kind == 'print':
            observed = run.elapsed_us + ticks_diff(ticks_us(), run._last_us)
            run._history(run.effect_history, {'kind':'print', 'text':value,
                                              'observed_elapsed_us':observed})
            _print(value, end='')
        else:
            execute([], (kind, value))

    try:
        if options.get('trigger') is not None:
            run.start().wait()
        if run._eager_cancelled:
            return run
        source = _invoke(program, options.get('parameters') or {}, handle) if callable(program) else program
        iterator = iter(source)
        while not run._eager_cancelled:
            try:
                command = _advance(iterator, handle)
            except StopIteration:
                break
            if run._eager_cancelled:
                break
            execute([command])
        if not run._eager_cancelled:
            run._stop_timer()
            run._stop_dma()
            run._drive_final()
            run.state = 'done'
    except BaseException as error:
        if not run.closed:
            run._fail(error)
        raise
    finally:
        run.elapsed_us += ticks_diff(ticks_us(), run._last_us)
        run._eager_running = False
    return run
