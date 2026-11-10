"""One generator for board execution, desktop preview and parameter scans."""
from ctrlaer import sweep, OFF, print


def experiment(pulse_ms=20, sweep_ms=2, start_hz=90000, stop_hz=120000, rest_ms=100):
    print("adiabatic shot", pulse_ms, sweep_ms)
    yield sweep(start_hz, stop_hz, sweep_ms=sweep_ms), pulse_ms
    yield OFF, rest_ms
