# Blocking operations

This example demonstrates advanced control flow using blocking operations and external pin control. It implements a complex timing pattern with variable durations and coordinated actions.

## Code

```python
from ctrlaer import ON, OFF, mux, CtrlAer
from machine import Pin
from time import sleep

N = 50          # Number of steps
n = 10          # Repetitions per step
analysis_delay = 5
reaction_delay = 1
off_time = 250
on_time = 100

ctrlaer = CtrlAer(sm_number=0, base_pin=7, n_pins=2, freq=111_500)

air_control = Pin(16, Pin.OUT)
air_control.value(0)
sleep(2)

def amine():
    for i in range(N):
        amine_time = int(on_time * i / (N - 1))
        for j in range(n):
            yield OFF, on_time - amine_time
            yield ON, amine_time
            yield OFF, off_time
        ctrlaer.block()
        sleep(reaction_delay)
        air_control.value(1)
        sleep(analysis_delay)
        air_control.value(0)

def aldehyde():
    for i in range(N):
        aldehyde_time = int(on_time * (N - 1 - i) / (N - 1))
        for j in range(n):
            yield OFF, on_time - aldehyde_time
            yield ON, aldehyde_time
            yield OFF, off_time

prog = mux([amine(), aldehyde()])
ctrlaer.run(prog)
```

## How it works

### Setup
- Creates a controller with 2 pins starting at GP7
- Sets up a separate pin for manual control of solenoid valve on GP16
- Initializes timing parameters:
    - 50 steps with variable durations
    - Delays for reactions and analysis
    - Fixed off time and maximum on time

### Signal generation
- Two complementary generators are used to demonstrate changing stoichiometry
    - `amine()`: Increasing ON time
    - `aldehyde()`: Decreasing ON time
- Each step includes:
    - Variable ON time
    - Fixed OFF time
    - Optional repetitions


### Control flow
- Each amine cycle:
    1. Generate pulses with increasing duration
    2. Wait for completion (`block()`)
    3. Wait for reaction to complete
    4. Activate air control
    5. Wait for reaction products to clear out of the analyzer
    6. Deactivate air control
- Aldehyde runs independently with decreasing durations

## Hardware setup

### Components needed
- Raspberry Pi Pico
- 2 vibrating mesh atomizers and drivers
- Solenoid valve and MOSFET driver + freewheeling diode
- External power supply for valve and aerosol VMAs (typically 6-12V)

### Connections
```
Pico GP7  → Amine control
Pico GP8  → Aldehyde control
Pico GP16 → Solenoid valve
```
