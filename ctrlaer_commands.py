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

    - `freq`: positive carrier frequency in Hz, independent of the controller's
      integer-command frequency.
    - `duty`: ideal HIGH fraction, strictly between 0 and 1.
    - `phase`: underlying carrier's rising-edge offset in integrated cycles,
      from 0 inclusive to 1 exclusive. Zero places its first ideal rising edge
      at command start; complementary turn-on follows the duty boundary.
    - `complement`: false for the main carrier, true for its complementary
      partner. Use matching options on both partners in `mux()`.
    - `dead_time_ns`: non-negative delay from each ideal turn-on edge to actual
      turn-on, in nanoseconds. Turn-off stays at the ideal edge.

    Returns a command object, not a generator. Use `yield pulse(...), duration`
    in a DMA mode or analysis. Compilation checks parameters, active pulse widths,
    dead-time feasibility and the six-clock minimum merged interval; impossible
    waveforms raise `ValueError`. Phase restarts for each newly yielded command;
    chunking/mux subdivision of that command preserves its phase.
    """
    return _Carrier(freq, duty, phase, complement, dead_time_ns)


def chirp(start_freq, stop_freq, sweep_ms=20, duty=0.5, phase=0, complement=False, dead_time_ns=0):
    """Continuous-phase triangular frequency sweep within an ON command.

    sweep_ms is one traversal from start_freq to stop_freq. The return traversal
    takes the same time, repeating independently of the yielded pulse duration.

    `start_freq`/`stop_freq` are positive Hz; either may be larger. `sweep_ms` is
    positive milliseconds for **one** leg, not the ON duration. A complete
    out-and-back triangle takes `2 * sweep_ms`. Yielded duration can end partway
    through a leg or span many triangles. Equal endpoints produce a constant
    carrier. Edge times follow integrated frequency with continuous phase.

    `duty`, `phase`, `complement` and `dead_time_ns` have the same meaning as in
    `pulse`. Returns a command for `yield chirp(...), duration`; validation occurs
    during compilation. Electrical drive timing is defined here; the transducer's
    physical response is a separate measurement.
    """
    return _Carrier(start_freq, duty, phase, complement, dead_time_ns, stop_freq, sweep_ms)


def sleep(seconds):
    """Hold the last digital levels for non-negative finite seconds.

    Inside finite/buffered analysis this becomes a timed PIO hold; it does not
    extend a completed carrier's oscillation. Inside eager execution it runs in
    sequence after the previous command. Original FIFO execution drains queued
    commands before sleeping. Outside a managed generator it calls `time.sleep`.
    Returns `None`; invalid duration raises `ValueError`. Yield OFF/LOW before
    this call when the delay must be inactive.
    """
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not isfinite(seconds) or seconds < 0):
        raise ValueError("sleep duration must be finite and non-negative")
    if _handler is None:
        return _sleep(seconds)
    return _handler("sleep", seconds)


def print(*values, sep=" ", end="\n"):
    """Print values in managed playback order, using string `sep` and `end`.

    Finite/buffered compilation records the formatted text and a PIO marker;
    runtime service prints when it observes that marker, with interpreter
    notification latency. Eager execution prints before advancing to the next
    command. Original FIFO execution first drains prior output. Outside a
    managed generator this calls built-in `print`. Returns `None`; non-string
    separators/endings raise `TypeError`. Formatting itself happens during
    lookahead, so arbitrary `__str__` side effects are ordinary Python effects.
    """
    if not isinstance(sep, str) or not isinstance(end, str):
        raise TypeError("sep and end must be strings")
    if _handler is None:
        return builtins.print(*values, sep=sep, end=end)
    return _handler("print", sep.join(str(value) for value in values) + end)


def wait_for(pin, edge="rising", level=None, timeout_ms=None):
    """Wait on a GPIO edge/level at this point in a managed generator.

    Arguments are those of `Trigger`: GPIO0–29, rising/falling edge or `level=0/1`,
    and a positive software timeout in milliseconds or `None`. The preceding
    digital levels remain driven. An edge first requires the opposite level;
    pulses during prior output are not queued. Configure input direction/pulls
    before preparation. In a `mux`, effects are global barriers for that engine.

    Uses PIO waits in finite, buffered and eager execution. Analysis records the
    gate and its fixed control overhead, with unknown external waiting time
    additional. Returns `None`. Outside managed execution, or with the original
    FIFO engine, raises `RuntimeError`; invalid trigger arguments raise
    `ValueError`. Timeout supervision has soft-service/interpreter latency.
    """
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
