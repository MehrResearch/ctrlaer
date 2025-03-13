# Getting Started

## Basic Concepts

CtrlAer is built around three main concepts:

1. **Signal Generators**: Python generators that yield timing patterns
2. **Signal Multiplexing**: Combining multiple generators to control multiple pins
3. **PIO Execution**: Running the program on RP2040/RP2350 hardware

## Creating Signal Patterns

### Simple Patterns

The simplest way to create a signal pattern is to yield pairs of (state, duration):

```python
def blink_led():
    while True:
        yield HIGH, 1000  # Constant HIGH for 1 second
        yield LOW, 1000   # Constant LOW for 1 second
```

### Square Waves

For high-frequency applications, use `PULSE10` or `PULSE01`:

```python
def ultrasonic_burst():
    # Generate 40kHz square wave for 100ms, then off for 900ms
    while True:
        yield PULSE10, 100   # Square wave at freq Hz
        yield OFF, 900       # Off for 900ms
```

The square wave frequency is set when creating the controller:
```python
controller = CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=40000)  # 40kHz
```

### Multiple Pins

Each generator in a multiplexed program controls one pin:

```python
def motor_phase1():
    while True:
        yield PULSE10, 50
        yield OFF, 50

def motor_phase2():
    while True:
        yield PULSE01, 50  # 180° out of phase
        yield OFF, 50

# First pin controlled by phase1, second pin by phase2
progs = [motor_phase1(), motor_phase2()]
prog = mux(progs)
```

## Hardware Setup

### Pin Assignment

- Pins are assigned sequentially starting from `base_pin`
- Each generator in a multiplexed program controls one pin
- Example with `base_pin=2` and three generators:
  ```
  GP2 → First generator
  GP3 → Second generator
  GP4 → Third generator
  ```

### State Machine Selection

- RP2040 has 8 state machines (0-7)
- RP2350 has 12 state machines (0-11)
- State machines are grouped in blocks of 4
- All state machines in a block share the same frequency
- Use different blocks for different frequencies

## Common Patterns

### Blocking vs Non-blocking

```python
# Blocking (wait for completion)
controller.run(prog)

# Non-blocking with later synchronization
controller.run(prog, block=False)
# Do other things
controller.block()  # Wait for completion
```

### Frequency Control

```python
# Create controller at initial frequency
controller = CtrlAer(sm_number=0, base_pin=0, n_pins=2, freq=10000)

# Change frequency later (affects all pins in same PIO block)
controller.set_freq(20000)
```

### Finite vs Infinite Programs

```python
# Finite program (runs 10 times then stops)
def finite_pattern():
    for _ in range(10):
        yield ON, 100
        yield OFF, 100

# Infinite program (runs until stopped)
def infinite_pattern():
    while True:
        yield ON, 100
        yield OFF, 100
```

See the [API Reference](api-reference.md) for detailed documentation of all features.