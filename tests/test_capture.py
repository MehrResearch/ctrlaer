from array import array
import io
import unittest

from ctrlaer import Trace, compile_program, square_wave, mux, HIGH, OFF, wait_for


def pack(values, n_pins):
    per_word = 32 // n_pins
    shift = 32 - per_word * n_pins
    result = array('I')
    for start in range(0, len(values), per_word):
        word = 0
        for index, value in enumerate(values[start:start + per_word]):
            word |= value << (shift + index * n_pins)
        result.append(word)
    return result


def sampled(program, rate=1000000, milliseconds=None):
    samples = round((program.duration_ms if milliseconds is None else milliseconds) * rate / 1000)
    segments = iter(program.segments())
    current = next(segments)
    upcoming = next(segments, None)
    values = []
    for index in range(samples):
        tick = index * program.clock_hz / rate
        while upcoming is not None and tick >= upcoming[0]:
            current, upcoming = upcoming, next(segments, None)
        values.append(current[1])
    return Trace(pack(values, program.n_pins), program.n_pins, len(values), rate)


class TraceTests(unittest.TestCase):
    def test_capture_rate_decodes_signed_clock_divider_registers(self):
        from ctrlaer_capture import _sample_rate
        for clock in (125000000,150000000):
            for divisor in (1,31250,37500,65535):
                register=divisor<<16
                if register & 0x80000000:
                    register-=1<<32
                self.assertEqual(_sample_rate(clock,register),clock/(2*divisor))
            self.assertEqual(_sample_rate(clock,0),clock/131072)

    def test_three_channel_packing_and_last_partial_word(self):
        values = [i % 8 for i in range(23)]
        trace = Trace(pack(values, 3), 3, len(values), 1000000)
        self.assertEqual(list(trace.levels()), values)
        self.assertEqual(list(trace.edges(0))[:3], [(1, 1), (2, 0), (3, 1)])
        self.assertEqual(next(trace.timestamps(0)), (1000, 1))

    def test_trace_matches_the_exact_compiled_buffer(self):
        program = compile_program(mux([[(square_wave(10000,dead_time_ns=1000),2)],
            [(square_wave(10000,complement=True,dead_time_ns=1000),2)]]),n_pins=2)
        trace = sampled(program)
        result = trace.compare(program, tolerance_ns=1000)
        self.assertTrue(result['passed'], result)

    def test_wrong_frequency_or_missing_output_does_not_pass(self):
        intended = compile_program([(square_wave(10000),2)])
        wrong = sampled(compile_program([(square_wave(11000),2)]))
        self.assertFalse(wrong.compare(intended)['passed'])
        quiet = Trace(pack([0] * 2000, 1), 1, 2000, 1000000)
        self.assertFalse(quiet.compare(intended)['passed'])

    def test_constant_level_is_checked_even_when_there_are_no_edges(self):
        intended = compile_program([(HIGH,2)],freq=100000)
        quiet = Trace(pack([0] * 1000, 1), 1, 1000, 1000000)
        self.assertFalse(quiet.compare(intended)['passed'])
        self.assertTrue(sampled(intended).compare(intended)['passed'])

    def test_overrun_trace_cannot_validate_sample_timestamps(self):
        intended = compile_program([(OFF, 1)])
        trace = sampled(intended)
        trace.metadata['overrun'] = True
        with self.assertRaisesRegex(ValueError, 'overrun'):
            trace.compare(intended, require_complete=False)

    def test_empty_traces_and_unknown_gate_times_are_not_accepted(self):
        with self.assertRaises(ValueError):
            Trace(array('I'), 1, 0, 1).compare(compile_program([(OFF,1)]))
        def gated():
            wait_for(9)
            yield OFF,1
        with self.assertRaisesRegex(ValueError, 'gate_wait_ticks'):
            sampled(compile_program([(OFF,1)])).compare(compile_program(gated))

    def test_exports_are_standard_vcd_and_explicit_channel_csv(self):
        trace = Trace(pack([0, 1, 1, 0], 1), 1, 4, 1000000, ['gate / source'])
        csv, vcd = io.StringIO(), io.StringIO()
        trace.write_csv(csv)
        trace.write_vcd(vcd)
        self.assertIn('1,1000.000,0,1', csv.getvalue())
        self.assertIn('$timescale 1ns $end', vcd.getvalue())
        self.assertIn('gate___source', vcd.getvalue())
        self.assertIn('#3000\n0c0', vcd.getvalue())

    def test_quiet_partial_capture_is_inconclusive_not_a_full_pass(self):
        intended=compile_program([(square_wave(10000,phase=.75),1)])
        quiet=Trace(pack([0]*32,1),1,32,1000000)
        result=quiet.compare(intended)
        self.assertFalse(result['passed'])
        self.assertTrue(result['inconclusive'])
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(quiet.compare(intended,require_complete=False)['passed'])

    def test_trigger_may_omit_initial_quiet_dead_time(self):
        intended=compile_program([(square_wave(10000,dead_time_ns=200),1)])
        full=sampled(intended,rate=25000000,milliseconds=1.2)
        values=list(full.levels())[6:]
        triggered=Trace(pack(values,1),1,len(values),25000000)
        result=triggered.compare(intended,tolerance_ns=80)
        self.assertTrue(result['passed'],result)
        self.assertTrue(result['coverage']['initial_quiet_prefix_inferred'])

    def test_initial_high_edge_within_origin_resolution_is_not_duplicated(self):
        intended = compile_program([(square_wave(100000, duty=.25), 1)])
        full = sampled(intended, rate=25000000, milliseconds=1.2)
        values = list(full.levels())
        # The first falling edge is sampled one sample later, placing the
        # inferred initial rising edge 40 ns after an already-HIGH sample zero.
        values.insert(1, values[0])
        trace = Trace(pack(values, 1), 1, len(values), 25000000)
        self.assertTrue(trace.compare(intended, tolerance_ns=80)['passed'])


if __name__ == '__main__':
    unittest.main()
