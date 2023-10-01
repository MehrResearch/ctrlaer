from pico_ctrlaer import ticks, ON, OFF, run

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

run([prog1(), prog2()], 0, 0)
