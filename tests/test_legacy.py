import io
import itertools
import unittest

from ctrlaer import CtrlAer, HIGH, LOW, mux


class MuxTests(unittest.TestCase):
    def test_unequal_finite_channels_keep_the_long_tail(self):
        self.assertEqual(list(mux([iter([(HIGH, 10)]), iter([(HIGH, 25)])])),
                         [(15, 10), (10, 15)])

    def test_infinite_sibling_continues_after_finite_channel(self):
        result = list(itertools.islice(mux([
            iter([(HIGH, 10)]), itertools.repeat((HIGH, 20))]), 4))
        self.assertEqual(result, [(15, 10), (10, 10), (10, 20), (10, 20)])

    def test_zero_duration_is_skipped_without_ending_other_channels(self):
        self.assertEqual(list(mux([[(HIGH, 0), (LOW, 5)], [(HIGH, 10)]])),
                         [(10, 5), (10, 5)])

    def test_exhausted_and_empty_channels(self):
        self.assertEqual(list(mux([[], [(HIGH, 3)]])), [(10, 3)])
        self.assertEqual(list(mux([[], []])), [])

    def test_invalid_segments_rejected(self):
        for value in (-1, float('nan'), float('inf')):
            with self.subTest(duration=value), self.assertRaises(ValueError):
                list(mux([[(HIGH, value)]]))
        with self.assertRaises(ValueError):
            list(mux([[(4, 10)]]))


class FakeSM:
    def __init__(self, level=0):
        self.level = level
        self.words = []

    def tx_fifo(self):
        return self.level

    def put(self, value):
        self.words.append(value)
        self.level += 1


class StreamingTests(unittest.TestCase):
    def controller(self, level=0):
        controller = CtrlAer.__new__(CtrlAer)
        controller.n_pins = 1
        controller._freq = 100000
        controller.sm = FakeSM(level)
        return controller

    def test_nonblocking_requires_space_for_a_complete_pair(self):
        c = self.controller(7)
        program = iter([(HIGH, 10), (LOW, 20)])
        self.assertIs(c.run(program, block=False), program)
        self.assertEqual(c.sm.words, [])
        self.assertEqual(list(program), [(HIGH, 10), (LOW, 20)])

    def test_nonblocking_can_resume_exactly_where_it_stopped(self):
        c = self.controller(6)
        remaining = c.run(iter([(HIGH, 10), (LOW, 20)]), block=False)
        self.assertEqual(c.sm.words, [3, 999])
        c.sm.level = 0
        self.assertIsNone(c.run(remaining, block=False))
        self.assertEqual(c.sm.words, [3, 999, 0, 1999])

    def test_zero_duration_never_wraps_to_a_32_bit_delay(self):
        c = self.controller()
        c.run([(HIGH, 0), (LOW, 0), (HIGH, 1)])
        self.assertEqual(c.sm.words, [3, 99])

    def test_bad_command_is_rejected_before_writing_its_state(self):
        for command in ((HIGH, -1), (HIGH, 0.0001), (4, 1), (HIGH, 1.5)):
            with self.subTest(command=command):
                c = self.controller()
                with self.assertRaises(ValueError):
                    c.run([command], use_ms=False)
                self.assertEqual(c.sm.words, [])

    def test_listen_accepts_text_bytes_blank_lines_and_eof(self):
        for stream in (io.BytesIO(b'\n3,10\n0,20\nEND\n'),
                       io.StringIO('\n3,10\n0,20\nEND\n'),
                       io.StringIO('3,10\n0,20\n')):
            c = self.controller()
            c.listen(stream)
            self.assertEqual(c.sm.words, [3, 999, 0, 1999])


if __name__ == '__main__':
    unittest.main()
