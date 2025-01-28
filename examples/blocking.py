from ctrlaer import ON, OFF, mux, CtrlAer
from machine import Pin
from time import sleep

N = 50
n = 1
cycle_delay = 5
reaction_delay = 1
off_time = 250
on_time = 100

ctrlaer = CtrlAer(sm_number=0, base_pin=7, n_pins=2,freq=111_500)

air_control = Pin(16, Pin.OUT)
air_control.value(0)
sleep(2)

def amine():
    for i in range(N):
        amine_time = int(on_time * i / (N - 1))
        for j in range(n):
            print(f'{(i, j)}: amine {amine_time}')
            yield OFF, on_time - amine_time
            yield ON, amine_time
            yield OFF, off_time
        ctrlaer.block()
        sleep(reaction_delay)
        air_control.value(1)
        sleep(cycle_delay)
        air_control.value(0)

def aldehyde():
    for i in range(N):
        aldehyde_time = int(on_time * (N - 1 - i) / (N - 1))
        for j in range(n):
            print(f'{(i, j)}: aldehyde {aldehyde_time}')
            yield OFF, on_time - aldehyde_time
            yield ON, aldehyde_time
            yield OFF, off_time


prog = mux([amine(), aldehyde()])
            
# GP14:  aldehyde
# GP13:  amine
ctrlaer.run(prog)