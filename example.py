from pico_ctrlaer import ticks, ON, OFF, run

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

run([prog1(), prog2(), prog1(), prog2(), prog1()], 0, 0)
