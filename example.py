from pico_ctrlaer import ticks, ON, OFF, Piezo

pins = [0, 2, 3]
piezos = [Piezo(i, p) for i, p in enumerate(pins)]

def prog1():
    for _ in range(200):
        yield ON, ticks(0.050)
        yield OFF, ticks(0.070)

def prog2():
    for _ in range(200):
        yield ON, ticks(0.033)
        yield OFF, ticks(0.155)

def prog3():
    for _ in range(200):
        yield ON, ticks(0.47)
        yield OFF, ticks(0.092)

piezos[0].run(prog1)
piezos[1].run(prog2)
piezos[2].run(prog3)
