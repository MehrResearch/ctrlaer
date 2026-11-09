"""CtrlAer command vocabulary and a small scoped effect interpreter.

Signal programs still yield (command, duration) pairs. The helpers below also
let analysis interpret CtrlAer-owned sleep/print/wait calls without replacing
Python control flow or trying to suppress arbitrary user side effects.
"""
import builtins
from math import isfinite
from time import sleep as _sleep

_handler = None


class _Carrier:
    def __init__(self, freq, duty, phase, complement, dead_time_ns, end_freq=None, sweep_ms=None):
        self.freq, self.duty, self.phase = freq, duty, phase
        self.complement, self.dead_time_ns, self.end_freq = complement, dead_time_ns, end_freq
        self.sweep_ms = sweep_ms


class _Signal:
    def __init__(self, command, duration, offset=0):
        self.command, self.duration, self.offset = command, duration, offset


class _Parallel:
    def __init__(self, signals):
        self.signals = signals


def pulse(freq, duty=0.5, phase=0, complement=False, dead_time_ns=0):
    """A carrier command used as ``yield pulse(...), duration``.

    Complementary partners use the same options with complement=False/True.
    Both turn on dead_time_ns after their ideal edges; both are off in the gap.
    """
    return _Carrier(freq, duty, phase, complement, dead_time_ns)


def chirp(start_freq, stop_freq, sweep_ms=20, duty=0.5, phase=0, complement=False, dead_time_ns=0):
    """Continuous-phase triangular frequency sweep within an ON command.

    sweep_ms is one traversal from start_freq to stop_freq. The return traversal
    takes the same time, repeating independently of the yielded pulse duration.
    """
    return _Carrier(start_freq, duty, phase, complement, dead_time_ns, stop_freq, sweep_ms)


def sleep(seconds):
    """Sleep in seconds; analysis/preparation instead inserts a timed hold."""
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not isfinite(seconds) or seconds < 0):
        raise ValueError("sleep duration must be finite and non-negative")
    if _handler is None:
        return _sleep(seconds)
    return _handler("sleep", seconds)


def print(*values, sep=" ", end="\n"):
    """Print normally, or record a print effect while compiling a generator."""
    if not isinstance(sep, str) or not isinstance(end, str):
        raise TypeError("sep and end must be strings")
    if _handler is None:
        return builtins.print(*values, sep=sep, end=end)
    return _handler("print", sep.join(str(value) for value in values) + end)


def wait_for(pin, edge="rising", level=None, timeout_ms=None):
    """A hardware gate at this point in a managed generator's execution."""
    from ctrlaer_program import Trigger
    trigger = Trigger(pin, edge, level, timeout_ms)
    if _handler is None:
        raise RuntimeError("wait_for must run inside CtrlAer execution or analysis")
    return _handler("wait", trigger)


def _advance(iterator, handler):
    global _handler
    previous = _handler
    _handler = handler
    try:
        return next(iterator)
    finally:
        _handler = previous


def _invoke(factory, parameters, handler):
    global _handler
    previous = _handler
    _handler = handler
    try:
        return factory(**parameters)
    finally:
        _handler = previous
