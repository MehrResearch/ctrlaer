# Stepper Motor Control

This example demonstrates how to control a stepper motor using two phases. It creates alternating signals that can drive a simple bipolar stepper motor or two coils of a unipolar stepper motor.

## Code

```python
from ctrlaer import mux, HIGH, LOW, CtrlAer

def phase1(n_steps):
    for i in range(n_steps):
        yield HIGH, 50  # Energize for 50ms
        yield LOW, 50   # De-energize for 50ms

def phase2(n_steps):
    for i in range(n_steps):
        yield LOW, 50   # Start de-energized
        yield HIGH, 50  # Energize for 50ms

progs = [phase1(10), phase2(10)]
prog = mux(progs)

# Frequency not critical for this application
stepper = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=10000)
stepper.run(prog)
```

## How it works

### Phase generation
- `phase1()` and `phase2()` create complementary signals
- Each phase runs for a specified number of steps
- Each step consists of a HIGH and LOW period
- 50ms timing gives a step rate of 10 steps/second

### Signal pattern
 ```
 Phase 1: ▁▁▁▁█████▁▁▁▁█████▁▁▁▁
 Phase 2: ▁▁▁▁▁▁▁▁▁█████▁▁▁▁█████
 ```

### Motor control
- When Phase 1 is HIGH and Phase 2 is LOW, current flows in one direction
- When Phase 1 is LOW and Phase 2 is HIGH, current flows in the opposite direction
- This creates the alternating magnetic field needed to rotate the motor

## Hardware setup

### Components needed
- Raspberry Pi Pico
- Bipolar stepper motor or half of a unipolar stepper
- Motor driver (e.g., L298N, A4988)
- Power supply for the motor

### Connections
```
Pico GP0 → Driver Input 1
Pico GP1 → Driver Input 2
Driver Outputs → Motor Coils
```

### Safety notes
- Never connect the motor directly to the Pico
- Use a proper motor driver rated for your motor's current
- Ensure your power supply can handle the motor's requirements

## Modifications

### Changing speed

Adjust the timing in the phase functions:
```python
# Faster (20 steps/second)
yield HIGH, 25
yield LOW, 25

# Slower (5 steps/second)
yield HIGH, 100
yield LOW, 100
```

### Continuous rotation

For continuous rotation, use an infinite loop:
```python
def phase1():
    while True:
        yield HIGH, 50
        yield LOW, 50
``` 