from pico_ctrlaer import mux, HIGH, LOW, OFF, PULSE01, PULSE10, CtrlAer


def low_freq():
    while True:
        yield HIGH, 5
        yield LOW, 5

def phase1():
    while True:
        yield PULSE01, 5
        yield OFF, 1000

def phase2():
    while True:
        yield PULSE10, 5
        yield OFF, 1000

progs = [low_freq(), phase1(), phase2(), phase1(), phase2()]
prog = mux(progs)

speaker = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=40000)
speaker.run(prog)