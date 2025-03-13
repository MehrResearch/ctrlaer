# Examples

This page provides various examples demonstrating different features and use cases of CtrlAer.

## Stepper Motor Control

This example demonstrates how to control a stepper motor using two phases. It creates alternating signals that can drive a simple bipolar stepper motor or two coils of a unipolar stepper motor.

### Code

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

### How it works

#### Phase generation
- `phase1()` and `phase2()` create complementary signals
- Each phase runs for a specified number of steps
- Each step consists of a HIGH and LOW period
- 50ms timing gives a step rate of 10 steps/second

#### Signal pattern
 ```
 Phase 1: ▁▁▁▁█████▁▁▁▁█████▁▁▁▁
 Phase 2: ▁▁▁▁▁▁▁▁▁█████▁▁▁▁█████
 ```

#### Hardware setup

- Components needed:
  - Raspberry Pi Pico
  - Bipolar stepper motor or half of a unipolar stepper
  - Motor driver (e.g., L298N, A4988)
  - Power supply for the motor

- Connections:
  ```
  Pico GP0 → Driver Input 1
  Pico GP1 → Driver Input 2
  Driver Outputs → Motor Coils
  ```

- Safety notes:
  - Never connect the motor directly to the Pico
  - Use a proper motor driver rated for your motor's current
  - Ensure your power supply can handle the motor's requirements

## Ultrasonic Control

This example demonstrates how to generate audio and ultrasonic signals with precise timing control. The audio signal is at 100Hz, and the ultrasonic signal is at the PIO frequency `freq`. The latter is also generated with 180 degree phase shift for use with an H-bridge driver to drive an ultrasonic transducer array.

### Code

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

### How it works

#### Signal generation
- Three types of signals are generated:
    - `low_freq()`: A simple audio signal at 100Hz (5ms on, 5ms off)
    - `phase1()`: PULSE01 pattern with 1-second gaps
    - `phase2()`: PULSE10 pattern with 1-second gaps

#### Signal patterns
```
Carrier:   ▄▄▄▄▄▁▁▁▁▁▄▄▄▄▄▁▁▁▁▁
Phase 1:   ▁█▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▁▁▁
Phase 2:   ▁▁▁█▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▁
```

#### Hardware setup

- Components needed:
  - Raspberry Pi Pico
  - Ultrasonic transducers
  - Appropriate drivers/amplifiers if needed
  - Power supply

- Connections:
  ```
  GP0 → Audio amplifier
  GP1-4 → H-bridge
  ```

## Blocking Operations

This example demonstrates advanced control flow using blocking operations and external pin control. It implements a complex timing pattern with variable durations and coordinated actions.

### Code

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

### How it works

#### Setup
- Creates a controller with 2 pins starting at GP7
- Sets up a separate pin for manual control of solenoid valve on GP16
- Initializes timing parameters:
    - 50 steps with variable durations
    - Delays for reactions and analysis
    - Fixed off time and maximum on time

#### Signal generation
- Two complementary generators are used to demonstrate changing stoichiometry
    - `amine()`: Increasing ON time
    - `aldehyde()`: Decreasing ON time
- Each step includes:
    - Variable ON time
    - Fixed OFF time
    - Optional repetitions

#### Control flow
- Each amine cycle:
    1. Generate pulses with increasing duration
    2. Wait for completion (`block()`)
    3. Wait for reaction to complete
    4. Activate air control
    5. Wait for reaction products to clear out of the analyzer
    6. Deactivate air control
- Aldehyde runs independently with decreasing durations

#### Hardware setup

- Components needed:
  - Raspberry Pi Pico
  - 2 vibrating mesh atomizers and drivers
  - Solenoid valve and MOSFET driver + freewheeling diode
  - External power supply for valve and aerosol VMAs (typically 6-12V)

- Connections:
  ```
  Pico GP7  → Amine control
  Pico GP8  → Aldehyde control
  Pico GP16 → Solenoid valve
  ```

## Serial Command Control

This example demonstrates how to control CtrlAer through serial communication from a host computer. The Pico listens for commands in a specific format and executes them in real-time.

### Pico Code (listen.py)

```python
from pico_ctrlaer import CtrlAer
from sys import stdin

ctrlaer = CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=5000)

ctrlaer.listen(stdin)
```

### Host Computer Code (Python with PySerial)

```python
import serial
import time

# Open serial connection to Pico
ser = serial.Serial('/dev/ttyACM0', 115200)  # Adjust port as needed
# On Windows, this might be 'COM3' or similar
# On macOS, this might be '/dev/tty.usbmodem14101' or similar

# Wait for connection to establish
time.sleep(1)

# Define commands as (state, duration_ms)
commands = [
    (2, 100),   # ON for 100ms (PULSE10)
    (0, 200),   # OFF for 200ms
    (3, 150),   # HIGH for 150ms
    (0, 200),   # OFF for 200ms
    (1, 100),   # PULSE01 for 100ms
]

# Send commands
for state, duration in commands:
    cmd = f"{state},{duration}\r\n"
    ser.write(cmd.encode())
    # Wait for acknowledgment (optional)
    response = ser.readline().decode().strip()
    print(f"Sent: {cmd.strip()}, Response: {response}")

# End the listening session
ser.write(b"END\r\n")
ser.close()
```

### How it works

#### Command format
- Commands are sent as text in the format: `<state>,<duration>`
- `state`: Integer value corresponding to CtrlAer constants:
  - `0`: LOW/OFF (constant low)
  - `1`: PULSE01 (square wave starting low)
  - `2`: PULSE10/ON (square wave starting high)
  - `3`: HIGH (constant high)
- `duration`: Time in milliseconds
- Special command `END` terminates the listening session

#### Implementation details
- The `listen()` method on the CtrlAer instance creates a generator that:
  1. Reads lines from the input stream (stdin)
  2. Parses each line into a state and duration
  3. Yields these as commands to be executed
  4. Echoes back the command for acknowledgment
  5. Terminates when "END" is received

#### Example applications
- Remote control of experiments
- Integration with data acquisition systems
- Automated testing sequences
- Synchronized multi-device operation

#### Hardware setup
- Raspberry Pi Pico connected via USB to host computer
- Output devices connected to GP0 (adjust as needed)
- No additional components required for basic operation