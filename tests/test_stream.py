"""Chunk boundaries must disappear from the emitted electrical waveform."""
import unittest

from ctrlaer import pulse, chirp, HIGH, LOW, ON, OFF, mux, sleep, print as emit_print, wait_for
from ctrlaer_native import words
from ctrlaer_program import compile_program, MIN_TICKS, OUT_PINS, IRQ_DONE
from ctrlaer_stream import StreamCompiler, IRQ_GUARD, NOP


def decode(chunks, n_pins):
    elapsed, mask, edges = 0, 0, []
    for output, chunk in chunks:
        for offset in range(0, chunk.count, 2):
            instruction = output[offset] & 0xffff
            if instruction == OUT_PINS | n_pins:
                next_mask = output[offset] >> 16
                if next_mask != mask:
                    edges.append((elapsed, next_mask))
                    mask = next_mask
            elif instruction not in (IRQ_DONE, IRQ_GUARD, NOP):
                pass  # Nominal clock includes control packets, not external waits.
            elapsed += output[offset + 1] + MIN_TICKS
    return edges, elapsed


class StreamCompilerTests(unittest.TestCase):
    def compile(self, source, **options):
        capacity = options.pop('buffer_words', 512)
        compiler = StreamCompiler(source, buffer_words=capacity, **options)
        buffer = words(capacity)
        chunks = []
        while True:
            chunk = compiler.fill(buffer)
            if chunk is None:
                break
            chunks.append((list(buffer[:chunk.count]), chunk))
            self.assertLess(len(chunks), 20000)
        return compiler, chunks

    def check_waveform(self, source, **options):
        compiler, chunks = self.compile(source(), **options)
        finite = compile_program(source(), **{key:value for key,value in options.items()
                                            if key not in ('buffer_words', 'horizon_ms')}, max_words=1000000)
        expected, elapsed = decode([(finite.words, type('Finite', (), {'count':len(finite.words)}))],
                                   finite.n_pins)
        actual, total = decode(chunks, finite.n_pins)
        self.assertEqual(actual, expected)
        self.assertEqual(total, elapsed)
        self.assertTrue(chunks[-1][1].final)
        for output, chunk in chunks[:-1]:
            self.assertEqual(output[-2], IRQ_GUARD)
            self.assertEqual(chunk.ticks, sum(output[i]+6 for i in range(1, len(output), 2)))
        return compiler, chunks

    def test_long_carrier_and_triangle_chirp_survive_many_reused_blocks(self):
        for clock in (125000000, 150000000):
            for command in (ON, pulse(108050, duty=.4, phase=.2),
                            chirp(90000,120000,sweep_ms=2),
                            chirp(90100,123400,sweep_ms=1.7,phase=.25)):
                with self.subTest(clock=clock,command=command):
                    self.check_waveform(lambda: iter([(command, 100)]), clock_hz=clock)

    def test_sparse_holds_are_subdivided_without_extra_edges_or_time(self):
        _, chunks = self.check_waveform(lambda: iter([(HIGH,100),(LOW,100)]), buffer_words=128)
        self.assertGreater(len(chunks), 4)
        self.assertTrue(all(chunk.count<=6 for _,chunk in chunks[:-1]))

    def test_repeated_chirp_cache_restarts_each_shot_and_rejects_changed_settings(self):
        a=chirp(90000,120000,sweep_ms=2,duty=.4,phase=.25)
        b=chirp(120000,90000,sweep_ms=2,duty=.6,phase=.1)
        source=[(a,7.3),(OFF,1.1),(a,7.3),(OFF,1.1),(b,9),(OFF,1),(a,7.3)]
        self.check_waveform(lambda:iter(source),buffer_words=128)

    def test_repeated_settings_reuse_cursor_without_rebuilding_native_setup(self):
        for command in (pulse(108050),chirp(90000,120000,sweep_ms=2),
                        chirp(90001,120000,sweep_ms=2)):
            compiler=StreamCompiler([(command,20),(OFF,20),(command,20)],buffer_words=128)
            output=words(128)
            compiler.fill(output)
            cursor=compiler.cursor
            while compiler.command_index<3:compiler.fill(output)
            self.assertIs(compiler.cursor,cursor)

    def test_nonperiodic_chirp_lookup_reuse_and_native_rollover(self):
        a=chirp(90001,120000,sweep_ms=2,duty=.37,phase=.2)
        b=chirp(120000,90001,sweep_ms=1.713,duty=.6,phase=.1)
        self.check_waveform(lambda:iter([(a,25),(OFF,1),(a,25),(b,25)]),buffer_words=128)
        cursors=[StreamCompiler([(a,60000)]) for _ in range(2)]
        outputs=[words(4096),words(4096)]
        for compiler,output in zip(cursors,outputs):compiler.fill(output)
        shifted=cursors[1]
        shift=0x20000000
        shifted.elapsed+=shift
        shifted.command_time+=shift
        shifted.command_end+=shift
        shifted.cursor.state[13]+=shift
        chunks=[compiler.fill(output) for compiler,output in zip(cursors,outputs)]
        self.assertEqual(chunks[0].ticks,chunks[1].ticks)
        self.assertEqual(list(outputs[0][:chunks[0].count]),list(outputs[1][:chunks[1].count]))

    def test_cached_chirp_packet_rollover_preserves_every_interval(self):
        for options in ({},{'phase':.37,'duty':.3}):
            command=chirp(90000,120000,sweep_ms=2,**options)
            cursors=[StreamCompiler([(command,60000)]) for _ in range(2)]
            outputs=[words(4096),words(4096)]
            for compiler,output in zip(cursors,outputs):compiler.fill(output)
            shifted=cursors[1];shift=0x20000000
            shifted.elapsed+=shift;shifted.command_time+=shift;shifted.command_end+=shift
            shifted.cursor.state[13]+=shift
            chunks=[compiler.fill(output) for compiler,output in zip(cursors,outputs)]
            self.assertEqual(chunks[0].ticks,chunks[1].ticks)
            self.assertEqual(list(outputs[0][:chunks[0].count]),list(outputs[1][:chunks[1].count]))

    def test_managed_sleep_print_and_gates_keep_their_order(self):
        def program():
            yield HIGH, 10
            sleep(.03)
            emit_print('ready')
            wait_for(6, timeout_ms=100)
            yield LOW, 20
        _, chunks = self.check_waveform(program)
        events = [event for _,chunk in chunks for _,event in chunk.events]
        self.assertEqual([event['kind'] for event in events], ['print','gate_enter','gate_exit'])
        self.assertEqual(events[0]['text'], 'ready\n')
        self.assertEqual(events[1]['trigger']['pin'], 6)

    def test_infinite_program_has_bounded_lookahead_and_metadata(self):
        advanced = []
        def program():
            while True:
                wait_for(6)
                advanced.append(1)
                yield ON, 20
                yield OFF, 20
        compiler = StreamCompiler(program(), buffer_words=512)
        buffer = words(512)
        for _ in range(80):
            chunk = compiler.fill(buffer)
            self.assertFalse(chunk.final)
            self.assertLessEqual(len(chunk.events), 128)
            self.assertLessEqual(len(compiler.pending), 2)
        self.assertLess(len(advanced), 10)
        self.assertIsNone(compiler.edge_buffer)  # Fused pulse packing needs none.
        self.assertEqual(compiler.edge_capacity, 2048)

    def test_marker_ids_roll_over_without_increasing_the_packet_width(self):
        def program():
            emit_print('a')
            yield LOW, 20
            emit_print('b')
        compiler = StreamCompiler(program(), buffer_words=64)
        compiler.marker = 65534
        events=[]
        while not compiler.finished:
            chunk=compiler.fill(words(64))
            events.extend(marker for marker,_ in chunk.events)
        self.assertEqual(events,[65535,1])

    def test_integer_mux_and_rich_mux_match_finite_compilation(self):
        for rich in (False, True):
            def program():
                a = iter([(pulse(10000) if rich else ON, 20),(OFF,20)])
                b = iter([(OFF,10),(pulse(10000) if rich else ON,20)])
                return mux([a,b])
            self.check_waveform(program,n_pins=2,buffer_words=128)

    def test_an_effect_only_generator_must_yield_regularly(self):
        def program():
            for _ in range(100):
                sleep(.1)
            yield LOW,1
        with self.assertRaisesRegex(ValueError,'yield regularly'):
            StreamCompiler(program(),buffer_words=64).fill(words(64))

    def test_eof_applies_final_mask_once_and_zero_duration_preserves_level(self):
        _,chunks=self.check_waveform(lambda:iter([(HIGH,20),(pulse(108050),0)]),final_mask=1)
        self.assertEqual(sum(1 for _,chunk in chunks if chunk.final),1)

    def test_eager_fragments_keep_their_level_at_eof_and_during_managed_sleep(self):
        compiler,chunks=self.compile([(HIGH,10)],_keep_level=True,final_mask=0)
        self.assertEqual(chunks[-1][1].last_mask,1)
        self.assertEqual(chunks[-1][0][-4]>>16,1)
        def pause():
            sleep(.01)
            if False:yield LOW,1
        _,chunks=self.compile(pause(),_keep_level=True,_initial_mask=1)
        self.assertTrue(all(mask==1 for output,_ in chunks for mask in
                            [output[i]>>16 for i in range(0,len(output),2)
                             if output[i]&0xffff==OUT_PINS|1]))

    def test_long_duration_quantisation_does_not_round_large_float_tick_counts(self):
        compiler=StreamCompiler([],clock_hz=150000000)
        self.assertEqual(compiler._ticks(100,1000),15000000)
        self.assertEqual(compiler._ticks(1000,1000),150000000)
        self.assertEqual(compiler._ticks(100000.0,1000),15000000000)
        self.assertEqual(compiler._ticks(.001,1),150000)

    def test_reused_chirp_scratch_does_not_carry_phase_into_a_new_fragment(self):
        compiler,_=self.compile([(chirp(90000,120000,sweep_ms=2),5)])
        scratch=(compiler._edge_storage,compiler.edge_buffer,compiler.pack_state)
        source=[(HIGH,1),(chirp(90100,123400,sweep_ms=1.7,phase=.25),5)]
        replacement,chunks=self.compile(source,_scratch=scratch)
        self.assertIs(replacement.edge_buffer,compiler.edge_buffer)
        finite=compile_program(source)
        expected=decode([(finite.words,type('Finite',(),{'count':len(finite.words)}))],1)
        self.assertEqual(decode(chunks,1),expected)

    def test_long_command_crosses_native_and_python_integer_boundaries(self):
        clock=125000000
        compiler=StreamCompiler([(pulse(2000),60000)],clock_hz=clock,buffer_words=32)
        output=words(32)
        elapsed=mask=rises=0
        for _ in range(5000):
            chunk=compiler.fill(output)
            self.assertFalse(chunk.final)
            for position in range(0,chunk.count,2):
                if output[position]&0xffff==OUT_PINS|1:
                    next_mask=output[position]>>16
                    if next_mask and not mask:
                        self.assertEqual(elapsed,rises*(clock//2000))
                        rises+=1
                    mask=next_mask
                elapsed+=output[position+1]+6
        self.assertGreater(elapsed,1<<31)
        self.assertGreater(compiler.cursor.origin,1<<30)


if __name__ == '__main__':
    unittest.main()
