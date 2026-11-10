"""Portable generator factories illustrated in docs/examples.md.

Compile on desktop Python or pass a factory to CtrlAer.prepare on the board.
The integer-command examples use a 40 kHz controller/analysis carrier.
"""
from ctrlaer import ON, OFF, pulse, mux, wait_for


def bursts(repeats=3):
    """Three 2 ms carrier bursts separated by 8 ms LOW by default."""
    for _ in range(repeats):
        yield ON, 2
        yield OFF, 8


def staggered(repeats=3):
    """Two 40 kHz burst trains; channel B starts 5 ms after channel A."""
    def a():
        for _ in range(repeats):
            yield ON, 2
            yield OFF, 8

    def b():
        for _ in range(repeats):
            yield OFF, 5
            yield ON, 2
            yield OFF, 3

    return mux([a(), b()])


def complementary(freq=40000, duty=.4, pulse_ms=.2, dead_time_ns=200):
    """One two-channel burst, with a LOW gap on every complementary handover."""
    a = pulse(freq, duty=duty, dead_time_ns=dead_time_ns)
    b = pulse(freq, duty=duty, complement=True, dead_time_ns=dead_time_ns)
    return mux([[(a, pulse_ms)], [(b, pulse_ms)]])


def triggered(n_shots=2):
    """Wait on GP6 before each 20 ms drive burst on GP0; rest 100 ms LOW."""
    for _ in range(n_shots):
        wait_for(6, timeout_ms=None)
        yield pulse(108050), 20
        yield OFF, 100
