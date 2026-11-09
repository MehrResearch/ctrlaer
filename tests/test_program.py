import contextlib
import io
import itertools
import unittest
from unittest.mock import patch

import ctrlaer
from ctrlaer import (HIGH, LOW, OFF, ON, chirp, pulse, mux, compile_program,
                     sleep, print as event_print, wait_for)
from ctrlaer_program import MAX_TICKS, MIN_TICKS


class CompilationTests(unittest.TestCase):
    def test_generators_are_the_only_public_authoring_interface(self):
        for name in ('Timeline', 'Wave', 'Parameter'):
            self.assertFalse(hasattr(ctrlaer, name), name)

    def test_constant_holds_merge_and_final_mask_occurs_at_the_end(self):
        p = compile_program([(5, 10), (5, 20), (10, 5)], n_pins=2, freq=100000, final_mask=3)
        self.assertEqual(list(p.segments()), [(0, 1, 3750000),
                         (3750000, 2, 625000), (4375000, 3, MIN_TICKS)])
        self.assertEqual(p.duration_ms, 35)
        self.assertEqual(p.pulse_counts, [1, 1])
        p.validate()

    def test_legacy_bit_planes_match_the_existing_pio(self):
        p = compile_program([(0b1001, 2)], n_pins=2, freq=100000, use_ms=False)
        self.assertEqual([(mask, ticks) for _, mask, ticks in p.segments()][:-1],
                         [(1, 625), (2, 625), (1, 625), (2, 625)])

    def test_infinite_generator_has_an_exact_finite_window(self):
        p = compile_program(itertools.repeat((HIGH, 10)), window_ms=25)
        self.assertEqual(p.duration_ticks, 3125000)
        self.assertEqual(p.summary()['requested_duration_ms'], 25)
        self.assertEqual(p.summary()['duration_quantisation_ns'], 0)
        self.assertTrue(p.windowed)

    def test_limits_fail_instead_of_silent_truncation(self):
        with self.assertRaisesRegex(ValueError, 'max_segments'):
            compile_program(itertools.repeat((HIGH, 10)), max_segments=3)
        with self.assertRaisesRegex(ValueError, 'max_words'):
            compile_program([(pulse(100000), 10)], max_words=32)

    def test_zero_and_negative_legacy_durations(self):
        p = compile_program([(HIGH, 0), (LOW, 2)])
        self.assertEqual(p.pulse_counts, [0])
        for value in (-1, float('nan'), float('inf'), .00001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compile_program([(HIGH, value)])

    def test_long_holds_split_without_overflow_or_tiny_remainders(self):
        p = compile_program([(HIGH, (MAX_TICKS + 1) * 1000 / 125000000)], freq=125000000)
        segments = list(p.segments())[:-1]
        self.assertEqual(sum(n for _, _, n in segments), MAX_TICKS + 1)
        self.assertTrue(all(MIN_TICKS <= n <= MAX_TICKS for _, _, n in segments))
        p.validate()

    def test_named_parameters_are_normal_generator_arguments(self):
        def program(duration):
            if not 1 <= duration <= 100:
                raise ValueError('duration out of range')
            yield HIGH, duration
        with self.assertRaises(TypeError):
            compile_program(program)
        with self.assertRaises(ValueError):
            compile_program(program, parameters={'duration': .5})
        self.assertEqual(compile_program(program, parameters={'duration': 20}, freq=100000).duration_ms, 20)

    def test_window_does_not_advance_the_next_generator_command(self):
        commands = iter([(HIGH, 10), (LOW, 20)])
        compile_program(commands, window_ms=10, freq=100000)
        self.assertEqual(next(commands), (LOW, 20))

    def test_zero_window_does_not_wait_for_an_external_trigger(self):
        p=compile_program(itertools.repeat((HIGH,1)),window_ms=0,trigger=ctrlaer.Trigger(9))
        self.assertEqual(p.duration_ticks,0)
        self.assertEqual(p.gates,[])

    def test_sub_minimum_intervals_fail_before_execution(self):
        with self.assertRaisesRegex(ValueError, 'edge interval'):
            compile_program([(pulse(120000, duty=.9999), 1)])


class EffectTests(unittest.TestCase):
    def test_analysis_interprets_own_sleep_print_and_wait(self):
        def program():
            event_print('before')
            yield HIGH, 1
            sleep(.01)
            wait_for(9, timeout_ms=50)
            event_print('after', end='!')
            yield OFF, 1
        output = io.StringIO()
        with patch('ctrlaer_commands._sleep', side_effect=AssertionError('real sleep')), contextlib.redirect_stdout(output):
            p = compile_program(program, freq=100000)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(p.summary()['requested_duration_ms'], 12)
        self.assertEqual([e['kind'] for e in p.events], ['print', 'gate_enter', 'gate_exit', 'print'])
        self.assertEqual(p.events[-1]['text'], 'after!')
        self.assertEqual(len(p.gates), 1)
        p.validate()

    def test_arbitrary_user_side_effects_are_not_suppressed(self):
        effects = []
        def program():
            effects.append('ordinary Python')
            yield HIGH, 1
        compile_program(program)
        self.assertEqual(effects, ['ordinary Python'])

    def test_handler_is_restored_after_an_exception(self):
        def broken():
            event_print('recorded')
            raise ValueError('user error')
            yield OFF, 1
        with self.assertRaises(ValueError):
            compile_program(broken)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            event_print('normal again')
        self.assertEqual(output.getvalue(), 'normal again\n')

    def test_effect_only_infinite_generator_is_bounded(self):
        def program():
            while True:
                sleep(.001)
            yield OFF, 1
        p = compile_program(program, window_ms=3)
        self.assertEqual(p.duration_ms, 3)
        p.validate()

    def test_sleep_quantisation_error_is_carried_between_calls(self):
        def program():
            for _ in range(200):
                sleep(60e-9)
            if False:
                yield OFF, 1
        p = compile_program(program)
        self.assertEqual(p.duration_ticks, 1500)
        self.assertLess(abs(p.summary()['duration_quantisation_ns']), .0001)


class WaveTests(unittest.TestCase):
    def test_duty_and_phase(self):
        p = compile_program([(pulse(100000, duty=.25, phase=.5), 1)])
        self.assertEqual(list(p.edges(0))[:4], [(625, 1), (938, 0), (1875, 1), (2188, 0)])
        self.assertEqual(p.pulse_counts, [100])

    def test_mux_independent_carriers(self):
        p = compile_program(mux([[(pulse(100000), 2)], [(pulse(40000), 2)]]), n_pins=2)
        self.assertEqual(p.pulse_counts, [200, 80])
        for pin, period in ((0, 1250), (1, 3125)):
            rises = [t for t, level in p.edges(pin) if level]
            self.assertTrue(all(b-a == period for a,b in zip(rises,rises[1:])))

    def test_complementary_dead_time(self):
        p = compile_program(mux([[(pulse(100000, dead_time_ns=200), 1)],
                                 [(pulse(100000, complement=True, dead_time_ns=200), 1)]]), n_pins=2)
        segments = list(p.segments())
        self.assertFalse(any(mask == 3 for _,mask,_ in segments))
        self.assertEqual(segments[:4], [(0,0,25),(25,1,600),(625,0,25),(650,2,600)])
        self.assertEqual(p.pulse_counts, [100,100])

    def test_triangular_sweep_rate_is_independent_of_on_duration(self):
        long = compile_program([(chirp(90000,120000,sweep_ms=2),20)])
        short = compile_program([(chirp(90000,120000,sweep_ms=2),2)])
        self.assertEqual(long.duration_ticks,2500000)
        self.assertEqual(long.pulse_counts,[2100])
        self.assertEqual([(t,v) for t,v in long.edges(0) if t<short.duration_ticks],
                         [(t,v) for t,v in short.edges(0) if t<short.duration_ticks])
        rises=[t for t,v in long.edges(0) if v]
        self.assertEqual([rises[i] for i in (0,210,420,630,840)], [0,250000,500000,750000,1000000])
        periods=[b-a for a,b in zip(rises,rises[1:])]
        self.assertGreater(periods[0],periods[209])
        self.assertLess(periods[210],periods[419])
        self.assertLess(len(long.words)*4,40000)
        long.validate()

    def test_mux_boundaries_do_not_restart_a_chirp(self):
        carrier=chirp(90000,120000,sweep_ms=2)
        alone=compile_program([(carrier,20)])
        merged=compile_program(mux([[(carrier,20)],itertools.repeat((OFF,1))]),n_pins=2,window_ms=20)
        self.assertEqual(list(alone.edges(0)),list(merged.edges(0)))
        merged.validate()

    def test_descending_sweep_and_impossible_dead_time(self):
        p=compile_program([(chirp(120000,90000,sweep_ms=2),4)])
        rises=[t for t,v in p.edges(0) if v]
        self.assertLess(rises[1]-rises[0],rises[209]-rises[208])
        with self.assertRaisesRegex(ValueError,'no active pulse'):
            compile_program([(pulse(100000,dead_time_ns=5000),1)])

    def test_fractional_cycle_reversals_preserve_integrated_phase(self):
        import math
        f0,f1,leg=90100,123400,.0017
        p=compile_program([(chirp(f0,f1,sweep_ms=leg*1000,phase=.25),17)])
        rises=[tick for tick,value in p.edges(0) if value]
        for index,tick in enumerate(rises):
            time=tick/p.clock_hz;part=math.floor(time/leg);local=time-part*leg
            start=f1 if part%2 else f0;slope=(f0-f1 if part%2 else f1-f0)/leg
            phase=part*(f0+f1)*leg/2+start*local+.5*slope*local*local
            self.assertLessEqual(abs(phase-index-.25),max(f0,f1)/p.clock_hz/2+1e-6)


if __name__ == '__main__':
    unittest.main()
