# Ultrasonic control

This example demonstrates how to generate audio and ultrasonic signals with precise timing control. The audio signal is at 100Hz, and the ultrasonic signal is at the PIO frequency `freq`. The latter is also generated with 180 degree phase shift for use with an H-bridge driver to drive an ultrasonic transducer array.

## Code

```python
from ctrlaer import mux, HIGH, LOW, OFF, PULSE01, PULSE10, CtrlAer

def low_freq():
    while True:
        yield HIGH, 5  # 5ms on
        yield LOW, 5   # 5ms off

def phase1():
    while True:
        yield PULSE01, 10    # 10ms pulse
        yield OFF, 1000     # 1s off

def phase2():
    while True:
        yield PULSE10, 10    # 10ms pulse
        yield OFF, 1000     # 1s off

progs = [low_freq(), phase1(), phase2(), phase1(), phase2()]
prog = mux(progs)

speaker = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=40000)
speaker.run(prog)
```

## How it works

### Signal generation
- Three types of signals are generated:
    - `low_freq()`: A simple audio signal at 100Hz (5ms on, 5ms off)
    - `phase1()`: PULSE01 pattern with 1-second gaps
    - `phase2()`: PULSE10 pattern with 1-second gaps

### Signal patterns
```
Carrier:   ▄▄▄▄▄▁▁▁▁▁▄▄▄▄▄▁▁▁▁▁
   Phase 1:   ▁█▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▁▁▁
   Phase 2:   ▁▁▁█▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▁
 ```

### Timing control
- Operating at 40kHz for precise ultrasonic timing
- Short pulses (5ms) for phase control
- Long gaps (1000ms) between pulses

## Hardware setup

### Components needed
- Raspberry Pi Pico
- Ultrasonic transducers
- Appropriate drivers/amplifiers if needed
- Power supply

### Connections
```
GP0 → Audio amplifier
GP1-4 → H-bridge
```

## Modifications

### Changing Frequency

Adjust the controller frequency:
```python
# Higher frequency (100kHz)
speaker = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=100000)

# Lower frequency (20kHz)
speaker = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=20000)
```

### Generating pulse patterns

```python
def custom_phase():
    while True:
        yield PULSE10, 2    # 2ms pulse train
        yield PULSE01, 2    # 2ms pulse train (phase shifted)
        yield OFF, 500      # 500ms gap
```

### Low-frequency pulse patterns

```python
def low_freq_burst():
    while True:
        # Burst of 10 pulses
        for _ in range(10):
            yield HIGH, 5
            yield LOW, 5
        # Long gap
        yield OFF, 2000
```
