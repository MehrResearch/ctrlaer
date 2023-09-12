from pico_ctrlaer import ticks, ON, OFF, Piezo

pins = [25, 14, 3]
piezos = [Piezo(i, p) for i, p in enumerate(pins)]

def prog1():
    for _ in range(200):
        yield ON, ticks(0.050)
        yield OFF, ticks(0.070)

def prog2():
    for _ in range(200):
        yield ON, ticks(0.066)
        yield OFF, ticks(0.155)

def prog3():
    for _ in range(50):
        yield ON, ticks(0.07)
        yield OFF, ticks(0.092)
        yield ON, ticks(0.10)
        yield OFF, ticks(0.13)  

piezos[0].run(prog1)
piezos[1].run(prog2)
piezos[2].run(prog3)
