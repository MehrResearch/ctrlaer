from pico_ctrlaer import mux, ON, OFF, CtrlAer, always_on, inactive

def prog1():
    for _ in range(300):
        yield ON, 50
        yield OFF, 70

def prog2():
    for _ in range(200):
        yield ON, 66
        yield OFF, 155

def prog3():
    for _ in range(50):
        yield ON, 70
        yield OFF, 92
        yield ON, 100
        yield OFF, 130

# Base pin compatible with Pimoroni Tiny RP2040
progs = [prog1(), prog2(), prog1(), prog2(), prog1(), always_on, inactive]
prog = mux(progs)
ctrlaer = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs))
ctrlaer.run(prog)