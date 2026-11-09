"""Check native arithmetic against the analytic compiler, not against itself."""
from array import array
import random
import unittest

from ctrlaer import chirp, pulse, compile_program
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
        commands = [pulse(108050), chirp(90000, 120000, sweep_ms=2),
                    chirp(120000, 90000, sweep_ms=2),
                    chirp(90100, 123400, sweep_ms=1.7, phase=.25)]
        for _ in range(30):
            kwargs = dict(duty=rng.uniform(.1,.9),phase=rng.uniform(0,1),
                          complement=rng.choice([False,True]),dead_time_ns=rng.choice([0,200]))
            if rng.choice([False,True]):
                commands.append(pulse(rng.randint(80000,140000),**kwargs))
            else:
                commands.append(chirp(rng.randint(80000,140000),rng.randint(80000,140000),sweep_ms=2,**kwargs))
        for clock in (125000000,150000000):
            for command in commands:
                with self.subTest(clock=clock,options=vars(command)):
                    expected=[int(time*clock+.5) for time,pin,level in
                              _analytic_carrier_events(command,.020,0,0,clock)]
                    observed=[tick for tick in self.ticks(command,clock,count=6000) if tick<clock//50]
                    self.assertEqual(len(observed),len(expected))
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(observed,expected)),1)

    def test_partitioning_does_not_change_phase_or_sweep_progress(self):
        for command in (pulse(108050,duty=.4,phase=.2),
                        chirp(90100,123400,sweep_ms=1.7,phase=.25),
                        chirp(120000,90000,sweep_ms=2,complement=True)):
            self.assertEqual(self.ticks(command,125000000,count=5000),
                             self.ticks(command,125000000,parts=[1,7,511,1024,2,3455]))

    def test_native_coordinate_rollover_preserves_absolute_time(self):
        command=pulse(108050)
        cursor=CarrierEdges.create(command,125000000)
        cursor.state[13]=0x20000000
        output=words(4);cursor.fill_into(output,4)
        shifted=[cursor.origin+tick for tick in output]
        baseline=[0x20000000+tick for tick in self.ticks(command,125000000,count=4)]
        self.assertEqual(shifted,baseline)

    def test_periodic_chirp_cache_repeats_carrier_phase_and_is_bounded(self):
        for clock in (125000000, 150000000):
            for command in (chirp(90000, 120000, sweep_ms=2, phase=.37, duty=.3),
                            chirp(120000, 90000, sweep_ms=2, complement=True,
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
                self.assertEqual(sum(len(part) for part in cursor._cache), 1680)
                # Skip ahead to an exact cached period after the native signed
                # coordinate limit, without allocating a several-second trace.
                group = 0x20000000 // cursor._period + 1
                cursor._cache_position = group * cursor._period_edges
                cursor.fill_into(output, 2)
                self.assertEqual(cursor.origin, group * cursor._period)
                self.assertEqual(list(output[:2]),
                                 [tick - cursor._period for tick in cursor._cache[1][:2]])

    def test_triangle_frequency_without_repeating_phase_does_not_enable_cache(self):
        cursor = CarrierEdges.create(chirp(90001, 120000, sweep_ms=2), 125000000)
        self.assertEqual(cursor._period, 0)
        self.assertIsNone(cursor._cache)

    def test_unsupported_ranges_decline_the_fast_path(self):
        for command in (pulse(10),pulse(100000,duty=.999),
                        chirp(90000,120000,sweep_ms=1e-9)):
            self.assertIsNone(CarrierEdges.create(command,125000000))


class NativePacketTests(unittest.TestCase):
    def test_packet_scan_counts_elapsed_time_and_rises(self):
        program=compile_program([(pulse(108050),20)])
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
