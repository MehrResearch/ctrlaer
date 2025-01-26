from pico_ctrlaer import mux, HIGH, LOW, CtrlAer

def phase1(n_steps):
    for i in range(n_steps):
        yield HIGH, 50
        yield LOW, 50

def phase2(n_steps):
    for i in range(n_steps):
        yield LOW, 50
        yield HIGH, 50

progs = [phase1(10), phase2(10)]
prog = mux(progs)

# `freq` not important in this case
stepper = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=10000)
stepper.run(prog)