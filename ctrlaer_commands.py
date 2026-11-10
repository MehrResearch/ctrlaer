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


def square_wave(freq, duty=0.5, phase=0, complement=False, dead_time_ns=0):
    """Create a repeating HIGH/LOW signal at a fixed frequency.

    Use this command in a generator, for example
    `yield square_wave(freq=40_000), 2` for 2 ms of a 40 kHz square wave.
    A single HIGH interval is a pulse; this command produces a train of pulses.

    Args:
        freq (float): Frequency in hertz, greater than zero.
        duty (float): Fraction of a cycle spent HIGH before any turn-on delay.
            Must be between 0 and 1; the default 0.5 gives equal HIGH and LOW times.
        phase (float): Delay before the first ideal rising edge, measured in cycles.
            Use 0 to start immediately, or 0.25 for a quarter-cycle delay. Range: 0–1,
            excluding 1.
        complement (bool): Produce the opposite half of the waveform. Give both
            partners the same frequency, duty and phase.
        dead_time_ns (float): Delay each turn-on by this many nanoseconds, leaving
            the turn-off time unchanged. Two complementary outputs are both LOW
            during this delay. The default is zero.

    Returns:
        A signal command to yield with its duration. It does not start an output.

    Notes:
        This command currently uses prepared, buffered or eager execution.
        Each new command starts with its own phase. Splitting a command into
        internal buffers, or combining it with another channel, preserves its phase.
        Impossible duty/delay settings and output changes closer than six chip-clock
        ticks are rejected when the program is prepared.
    """
    return _Carrier(freq, duty, phase, complement, dead_time_ns)


def sweep(start_freq, stop_freq, sweep_ms=20, duty=0.5, phase=0, complement=False, dead_time_ns=0):
    """Create a square wave whose frequency repeatedly sweeps between two values.

    The frequency changes steadily from `start_freq` to `stop_freq`, then returns
    in the same time. This up-and-down pattern repeats for as long as the command
    lasts. The waveform continues smoothly through each change of direction.

    Args:
        start_freq (float): Starting frequency in hertz, greater than zero.
        stop_freq (float): Frequency at the other end of the sweep, greater than
            zero. It may be higher or lower than the starting frequency.
        sweep_ms (float): Time for one journey between the two frequencies, in
            milliseconds. A complete out-and-back sweep takes twice this time.
            The default is 20 ms.
        duty (float): HIGH fraction of each cycle, as in `square_wave`.
        phase (float): Initial delay in cycles, as in `square_wave`.
        complement (bool): Select the complementary output, as in `square_wave`.
        dead_time_ns (float): Turn-on delay in nanoseconds, as in `square_wave`.

    Returns:
        A signal command to yield with a duration independent of `sweep_ms`.

    Examples:
        ```python
        # 20 ms of output: five complete 2 ms up / 2 ms down sweeps.
        yield sweep(start_freq=90_000, stop_freq=120_000, sweep_ms=2), 20
        ```

    Notes:
        A command can end halfway through a sweep. Equal frequencies give a fixed
        square wave. Execution requirements and timing checks are the same as for
        `square_wave`. The command sets the electrical signal; choosing a suitable
        frequency range for a transducer requires measuring that device.
    """
    return _Carrier(start_freq, duty, phase, complement, dead_time_ns, stop_freq, sweep_ms)


def sleep(seconds):
    """Keep the outputs at their last levels for the given number of seconds.

    Args:
        seconds (float): A finite, non-negative time in seconds.

    Returns:
        `None`.

    Notes:
        In a CtrlAer program this delay belongs to the experiment's timing.
        A completed square wave does not keep oscillating during the delay: the
        last HIGH or LOW level is held. Yield `OFF` first when you want a LOW pause.
        Outside a CtrlAer program, this calls `time.sleep(seconds)`.
        Invalid durations raise `ValueError`.
    """
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not isfinite(seconds) or seconds < 0):
        raise ValueError("sleep duration must be finite and non-negative")
    if _handler is None:
        return _sleep(seconds)
    return _handler("sleep", seconds)


def print(*values, sep=" ", end="\n"):
    """Print a message when playback reaches this point in the experiment.

    Args:
        values (objects): Values to print, separated by `sep`.
        sep (str): Text between values; the default is a space.
        end (str): Text after the message; the default is a newline.

    Returns:
        `None`.

    Notes:
        This follows the familiar built-in `print` syntax. Inside a prepared or
        buffered program the message is associated with a point in the output
        sequence. Python displays it when it handles that notification, so the
        console time is not a precise measurement of a pin change.
        Values are converted to text while Python reads the generator. Outside a
        CtrlAer program, this calls the built-in `print` directly. `sep` and `end`
        must be strings, otherwise `TypeError` is raised.
    """
    if not isinstance(sep, str) or not isinstance(end, str):
        raise TypeError("sep and end must be strings")
    if _handler is None:
        return builtins.print(*values, sep=sep, end=end)
    return _handler("print", sep.join(str(value) for value in values) + end)


def wait_for(pin, edge="rising", level=None, timeout_ms=None):
    """Pause the output sequence until an input signal changes or reaches a level.

    Args:
        pin (int): Input GPIO number, from 0 to 29. Configure it as an input before
            preparing the experiment.
        edge (str): `"rising"` for LOW followed by HIGH, or `"falling"` for HIGH
            followed by LOW. The default is `"rising"`.
        level (int or None): Set to 0 or 1 to wait for that level instead of an edge.
            A level already present lets the experiment continue immediately.
        timeout_ms (float or None): Maximum wait in milliseconds. The default
            `None` waits without a time limit.

    Returns:
        `None`.

    Notes:
        The preceding output levels remain in place while waiting. An edge wait
        must see the opposite level first: a HIGH input by itself is not a new
        rising edge. Pulses that occur before the wait begins are not saved.
        In a `mux()` program, the wait pauses all channels of that controller.
        Use this helper inside a prepared, buffered or eager generator; calling
        it outside a supported program raises `RuntimeError`. Invalid arguments
        raise `ValueError`. Timeout handling runs in Python and can be delayed if
        other Python code is blocking.
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
