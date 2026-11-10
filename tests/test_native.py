"""Check native arithmetic against the analytic compiler, not against itself."""
from array import array
import random
import unittest

from ctrlaer import sweep, square_wave, compile_program
from ctrlaer_native import CarrierEdges, words, _scan_outputs
from ctrlaer_program import _analytic_carrier_events


class NativeEdgesTests(unittest.TestCase):
    def ticks(self, command, clock, count=5000, parts=None):
        cursor = CarrierEdges.create(command, clock)
        self.assertIsNotNone(cursor)
        result = []
        for size in parts or [count]:
            buffer = words(size)
            self.assertEqual(cursor.fill_into(buffer, size), size)
            result.extend(tick + cursor.origin for tick in buffer)
        return result

    def test_native_edges_follow_the_analytic_carrier(self):
        rng = random.Random(20261109)
        commands = [square_wave(108050), sweep(90000, 120000, sweep_ms=2),
                    sweep(120000, 90000, sweep_ms=2),
                    sweep(90100, 123400, sweep_ms=1.7, phase=.25)]
        for _ in range(30):
            kwargs = dict(duty=rng.uniform(.1,.9),phase=rng.uniform(0,1),
                          complement=rng.choice([False,True]),dead_time_ns=rng.choice([0,200]))
            if rng.choice([False,True]):
                commands.append(square_wave(rng.randint(80000,140000),**kwargs))
            else:
                commands.append(sweep(rng.randint(80000,140000),rng.randint(80000,140000),sweep_ms=2,**kwargs))
        for clock in (125000000,150000000):
            for command in commands:
                with self.subTest(clock=clock,options=vars(command)):
                    expected=[int(time*clock+.5) for time,pin,level in
                              _analytic_carrier_events(command,.020,0,0,clock)]
                    observed=[tick for tick in self.ticks(command,clock,count=6000) if tick<clock//50]
                    self.assertEqual(len(observed),len(expected))
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(observed,expected)),1)

    def test_partitioning_does_not_change_phase_or_sweep_progress(self):
        for command in (square_wave(108050,duty=.4,phase=.2),
                        sweep(90100,123400,sweep_ms=1.7,phase=.25),
                        sweep(120000,90000,sweep_ms=2,complement=True)):
            self.assertEqual(self.ticks(command,125000000,count=5000),
                             self.ticks(command,125000000,parts=[1,7,511,1024,2,3455]))

    def test_native_coordinate_rollover_preserves_absolute_time(self):
        for command in (square_wave(108050),sweep(90100,123400,sweep_ms=1.7,phase=.25)):
            cursor=CarrierEdges.create(command,125000000)
            cursor.state[13]+=0x20000000
            output=words(4);cursor.fill_into(output,4)
            shifted=[cursor.origin+tick for tick in output]
            baseline=[0x20000000+tick for tick in self.ticks(command,125000000,count=4)]
            self.assertEqual(shifted,baseline)

    def test_periodic_chirp_cache_repeats_carrier_phase_and_is_bounded(self):
        for clock in (125000000, 150000000):
            for command in (sweep(90000, 120000, sweep_ms=2, phase=.37, duty=.3),
                            sweep(120000, 90000, sweep_ms=2, complement=True,
                                  dead_time_ns=200)):
                cursor = CarrierEdges.create(command, clock)
                self.assertEqual(cursor._period_edges, 840)
                output = words(840 * 8)
                cursor.fill_into(output, len(output))
                expected = [int(time * clock + .5) for time, _, _ in
                            _analytic_carrier_events(command, .032, 0, 0, clock)]
                observed = [tick for tick in output if tick < clock * .032]
                self.assertEqual(len(observed), len(expected))
                self.assertLessEqual(max(abs(a-b) for a,b in zip(observed, expected)), 1)
                self.assertEqual(len(cursor._cache[1]), 840)
                # Skip ahead to an exact cached period after the native signed
                # coordinate limit, without allocating a several-second trace.
                group = 0x20000000 // cursor._period + 1
                cursor._cache_position = group * cursor._period_edges
                cursor.fill_into(output, 2)
                self.assertEqual(cursor.origin, group * cursor._period)
                self.assertEqual(list(output[:2]),
                                 [cursor._cache[2],cursor._cache[2]+cursor._cache[1][0]+cursor._cache[3]])

    def test_default_sweep_uses_one_bounded_period_without_long_term_phase_error(self):
        for clock in (125000000,150000000):
            for options in ({'phase':.37,'duty':.3},
                            {'complement':True,'dead_time_ns':200}):
                command=sweep(90000,120000,**options)
                cursor=CarrierEdges.create(command,clock)
                self.assertEqual(cursor._period_edges,8400)
                output=words(8400*5)
                cursor.fill_into(output,len(output))
                expected=[round(t*clock) for t,_,_ in
                          _analytic_carrier_events(command,.16,0,0,clock)]
                observed=[t for t in output if t<clock*.16]
                self.assertEqual(len(observed),len(expected))
                self.assertLessEqual(max(abs(a-b) for a,b in zip(observed,expected)),1)
                self.assertLessEqual(len(cursor._cache[1])*2,20000)

    def test_compact_interval_cache_retains_exact_ticks_with_half_the_storage(self):
        for clock in (125000000,150000000):
            command=sweep(90000,120000)
            compact=CarrierEdges.create(command,clock)
            reference=CarrierEdges.create(command,clock)
            reference._cache_base=0
            actual,expected=words(10000),words(10000)
            compact.fill_into(actual,len(actual))
            reference.fill_into(expected,len(expected))
            self.assertEqual(actual,expected)
            self.assertEqual(len(compact._cache[0]),4200)
            self.assertEqual(len(reference._cache[0]),8400)

    def test_symmetric_chirp_cache_folds_only_aligned_half_cycles(self):
        for clock in (125000000,150000000):
            for options in ({},{'phase':.1},{'duty':.4},{'dead_time_ns':200}):
                command=sweep(90000,120000,**options)
                cursor=CarrierEdges.create(command,clock)
                actual=words(16800);cursor.fill_into(actual,len(actual))
                expected=[round(time*clock) for time,_,_ in
                          _analytic_carrier_events(command,.08,0,0,clock)]
                self.assertEqual(cursor._cache[4],not options)
                self.assertEqual(len(cursor._cache[1]),4200 if not options else 8400)
                self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1)

    def test_triangle_frequency_without_repeating_phase_does_not_enable_cache(self):
        cursor = CarrierEdges.create(sweep(90001, 120000, sweep_ms=2), 125000000)
        self.assertEqual(cursor._period, 0)
        self.assertIsNone(cursor._cache)

    def test_nonperiodic_lookup_preserves_phase_within_one_clock(self):
        for clock in (125000000,150000000):
            for sweep_ms in (1.713,2,20):
                command=sweep(90001,120000,sweep_ms=sweep_ms,duty=.37,phase=.3,
                              complement=True,dead_time_ns=200)
                cursor=CarrierEdges.create(command,clock)
                self.assertIsNotNone(cursor._lut)
                self.assertLessEqual(len(cursor._lut[0]),32784)
                seconds=sweep_ms*.006
                expected=[time*clock for time,_,_ in
                          _analytic_carrier_events(command,seconds,0,0,clock)]
                buffer=words(1024)
                actual=[]
                while not actual or actual[-1]<seconds*clock:
                    cursor.fill_into(buffer,len(buffer))
                    actual.extend(tick+cursor.origin for tick in buffer)
                actual=[tick for tick in actual if tick<seconds*clock]
                self.assertEqual(len(actual),len(expected))
                self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1)

    def test_unsupported_ranges_decline_the_fast_path(self):
        for command in (square_wave(10),square_wave(100000,duty=.999),
                        sweep(90000,120000,sweep_ms=1e-9)):
            self.assertIsNone(CarrierEdges.create(command,125000000))


class NativePacketTests(unittest.TestCase):
    def test_packet_scan_counts_elapsed_time_and_rises(self):
        program=compile_program([(square_wave(108050),20)])
        stats=array('I',[0,0,0,0]);pulses=array('I',[0])
        end=_scan_outputs(program.words,0,len(program.words)-4,1,pulses,stats)
        self.assertEqual(end,len(program.words)-4)
        self.assertEqual(stats[0]+(stats[1]<<32),program.duration_ticks)
        self.assertEqual(list(pulses),program.pulse_counts)

    def test_packet_scan_detects_mask_overflow_and_accumulates_64bit_time(self):
        stats=array('I',[0,0,0,0]);pulses=array('I',[0])
        packed=array('I',[0x6001 | (1<<16),0xffffffff,0x6001,1])
        self.assertEqual(_scan_outputs(packed,0,len(packed),1,pulses,stats),4)
        self.assertEqual(stats[0]+(stats[1]<<32),0xffffffff+13)
        bad=array('I',[0x6001 | (2<<16),0])
        self.assertEqual(_scan_outputs(bad,0,2,1,pulses,stats),0)
        self.assertEqual(stats[3],1)


if __name__=='__main__':unittest.main()
