"""Exercise the actual callback/lifecycle methods without requiring RP2 hardware.

Extracting the Python definitions avoids mocking MicroPython's native emitter.
Electrical output, native register writes and USB interrupts are tested on boards.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def definitions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    tree.body = [node for node in tree.body if getattr(node, 'name', None) in names]
    exec(compile(tree, filename, 'exec'), namespace)
    return namespace


class RuntimeInterruptTests(unittest.TestCase):
    def setUp(self):
        self.env = definitions('ctrlaer_runtime.py',
            {'PreparedRun', '_close_dma', 'ExecutionTimeoutError', 'UnderrunError'},
            {'ticks_ms': lambda: 0, 'number': Mock(), 'mem32': {}})
        self.prepared = self.env['PreparedRun']

    def test_keyboard_interrupt_in_soft_timer_is_retained_for_foreground(self):
        error = KeyboardInterrupt()
        run = SimpleNamespace(poll=Mock(side_effect=error), _fail=Mock())
        self.prepared._timer_service(run, None)
        run._fail.assert_called_once_with(error)

    def test_foreground_interrupt_aborts_even_without_callers_finally(self):
        run = SimpleNamespace(closed=False, state='running', active=True,
                              poll=Mock(side_effect=KeyboardInterrupt), abort=Mock())
        with self.assertRaises(KeyboardInterrupt):
            self.prepared.wait(run)
        run.abort.assert_called_once_with()

    def test_recorded_interrupt_is_raised_by_wait_without_second_abort(self):
        error = KeyboardInterrupt()
        run = SimpleNamespace(closed=False, state='error', active=False,
                              error=error, abort=Mock())
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.prepared.wait(run)
        self.assertIs(caught.exception, error)
        run.abort.assert_not_called()

    def test_all_owned_dma_channels_are_disabled_before_any_is_closed(self):
        calls = []
        self.env['_disable_dma'] = lambda mask: calls.append(('disable', mask))
        def dma(channel):
            return SimpleNamespace(channel=channel,
                                   close=lambda: calls.append(('close', channel)))
        self.env['_close_dma']((dma(2), None, dma(6)), (dma(9),))
        mask = (1 << 2) | (1 << 6) | (1 << 9)
        self.assertEqual(calls, [('disable', mask), ('close', 2), ('close', 6), ('close', 9)])
        self.assertEqual(self.env['mem32'], {0x50000400: mask})

    def test_hard_dma_callback_retains_interrupt_without_allocating_new_error(self):
        error = KeyboardInterrupt()
        self.env.update(ticks_us=lambda: 0, _publish_dma=Mock(side_effect=error))
        definitions('ctrlaer_buffered.py', {'BufferedRun'}, self.env)
        run = SimpleNamespace(_irq_state=[0]*72, _irq_map=[0]*16, _irq_exception=None)
        self.env['BufferedRun']._dma_irq(run, SimpleNamespace(channel=0))
        self.assertIs(run._irq_exception, error)
        self.assertEqual(run._irq_state[5], 3)

    def test_hard_callback_interrupt_has_priority_over_pio_guard(self):
        error = KeyboardInterrupt()
        self.env.update(ticks_us=lambda: 0, ticks_diff=lambda a, b: a-b,
                        mem32={0x30: 0x10})
        definitions('ctrlaer_buffered.py', {'BufferedRun'}, self.env)
        run = SimpleNamespace(active=True, _servicing=False, elapsed_us=0,
                              _last_us=0, _events={}, _irq_address=0x30,
                              _irq_exception=error, _fail=Mock(), state='running')
        self.env['BufferedRun'].poll(run)
        run._fail.assert_called_once_with(error)
        self.assertFalse(run._servicing)

    def test_compact_gate_metadata_preserves_public_histories_and_messages(self):
        from ctrlaer_program import Trigger
        self.env.update(ticks_ms=lambda: 10, _print=Mock())
        definitions('ctrlaer_buffered.py', {'BufferedRun'}, self.env)
        gate_history, effect_history = [], []
        trigger = Trigger(6, timeout_ms=100)
        run = SimpleNamespace(_receive_index=0, _expected_marker=1,
            _receive=[SimpleNamespace(words=[1,2,3]+[0]*253)],
            _events={1:('gate_enter',9,trigger,0), 2:('gate_exit',9),
                     3:('print','ready\n')}, elapsed_us=42,
            _history=lambda target,item: target.append(item),
            gate_history=gate_history, effect_history=effect_history)
        self.env['BufferedRun']._read_gate_events(run)
        self.assertEqual(gate_history,[{'gate':9,'entering':True,'observed_elapsed_us':42},
                                       {'gate':9,'entering':False,'observed_elapsed_us':42}])
        self.assertEqual(effect_history,[{'kind':'print','text':'ready\n','observed_elapsed_us':42}])
        self.assertIsNone(run._gate)
        self.assertEqual(run.state,'running')
        self.assertEqual(run._events,{})
        self.env['_print'].assert_called_once_with('ready\n',end='')

    def test_history_makes_room_before_appending_at_its_memory_limit(self):
        definitions('ctrlaer_buffered.py', {'BufferedRun'}, self.env)
        class BoundedList(list):
            def append(self, item):
                if len(self) >= 2:
                    raise MemoryError('would grow the backing array')
                super().append(item)
        history=BoundedList(['oldest','recent'])
        run=SimpleNamespace(history_limit=2)
        self.env['BufferedRun']._history(run,history,'new')
        self.assertEqual(history,['recent','new'])


if __name__ == '__main__':
    unittest.main()
