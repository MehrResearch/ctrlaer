# API reference

## Constants

### Pin states
- `HIGH` (0b11): Constant high output
- `LOW` (0b00): Constant low output (alias: `OFF`)
- `PULSE10` (0b10): Square wave at frequency `freq`, starting high (alias: `ON`)
- `PULSE01` (0b01): Square wave at frequency `freq`, starting low (180° phase shift)

### Default values
- `FREQ` (108,050): Default frequency in Hz for square wave generation. This value is used if no `freq` is specified in the CtrlAer constructor.

## Functions

### mux(progs)

Combines multiple generator programs into a single program that is applied to a contiguous set of pins, starting from `base_pin`. Each program in the input list controls one pin.

**Parameters:**
- `progs`: List of generator programs to combine

**Returns:**
- Generator yielding `(state, duration)` tuples

**Example:**
```python
progs = [program1(), program2()]  # program1 controls first pin, program2 controls second pin
combined = mux(progs)
```

## Classes

### CtrlAer

Main controller class for PIO-based timing control.

#### Constructor

```python
CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=FREQ)
```

**Parameters:**
- `sm_number` (int): PIO state machine number (0-7 on RP2040, 0-11 on RP2350)
- `base_pin` (int): First GPIO pin number to use for output
- `n_pins` (int): Number of consecutive pins to control
- `freq` (int, optional): Frequency in Hz for square wave generation. Defaults to 108,050 Hz.

**Notes:**
- State machines are grouped into PIO blocks (0-3, 4-7, 8-11)
- All state machines in the same PIO block share the same frequency
- Each PIO block can have its own frequency

**Example:**
```python
# Control 2 pins (GP5 and GP6) using state machine 1 at 40 kHz
ctrlaer = CtrlAer(sm_number=1, base_pin=5, n_pins=2, freq=40000)
```

#### Methods

##### run(prog, block=True, use_ms=True)

Runs a program on the PIO state machine.

**Parameters:**
- `prog`: Generator program yielding `(state, duration)` tuples
- `block` (bool, optional): Whether to block until program completion. Defaults to True.
- `use_ms` (bool, optional): If `True`, interpret durations as milliseconds; if `False`, interpret as raw PIO ticks. Defaults to True.

**Notes about timing:**
- When `use_ms=True`, durations are interpreted as milliseconds
- When `use_ms=False`, durations are interpreted as raw PIO ticks
- Example: At 100 kHz square wave frequency, 10000 ticks = 100 ms
- 1 tick = one square wave pulse period
- The current implementation of CtrlAer's PIO assembly takes 10 PIO clock cycles to execute, though this is an implementation detail that users shouldn't have to worry about.

**Example with millisecond timing (default):**
```python
ctrlaer = CtrlAer(..., freq=100000)  # 100 kHz
ctrlaer.run([
    (ON, 100),    # ON for 100 ms
    (OFF, 200)    # OFF for 200 ms
])
```

**Example with raw tick timing:**
```python
ctrlaer = CtrlAer(..., freq=100000)  # 100 kHz
# Exactly 10k square wave pulses followed by 20k periods of low
# This is equivalent to 100 ms ON and 200 ms OFF at 100 kHz but useful
# if pulse _count_ rather than total duration is important
ctrlaer.run([
    (ON, 10000),   # ON for 10000 ticks (100 ms at 100 kHz)
    (OFF, 20000)   # OFF for 20000 ticks (200 ms at 100 kHz)
], use_ms=False)
```

##### `block()`

Blocks until the current program completes (FIFO empty).

**Example:**
```python
ctrlaer.run(prog, block=False)
# Do other things
ctrlaer.block()  # Wait for completion
```

##### `set_freq(freq)`

Changes the square wave frequency for the PIO block containing this state machine.

**Parameters:**
- `freq` (int): New frequency in Hz

**Example:**
```python
ctrlaer.set_freq(20000)  # Set to 20kHz
```

##### `listen(io)`

Reads commands from an input stream and executes them in real-time.

**Parameters:**
- `io`: Input stream object with a `readline()` method (e.g., `sys.stdin` or `io.BytesIO`)

**Command format:**
- Each line should be in the format `<state>,<duration>` where:
  - `state`: Integer corresponding to pin state constants
    - For single pin: 0=OFF/LOW (0b00), 1=PULSE01 (0b01), 2=ON/PULSE10 (0b10), 3=HIGH (0b11)
    - For multiple pins: binary pattern where each 2 bits represent a pin state
  - `duration`: Time in milliseconds
- Special command `END` terminates the listening session

**Example with single pin:**
```python
from io import BytesIO
from sys import stdin

# Create controller for 1 pin
ctrlaer = CtrlAer(sm_number=0, base_pin=0, n_pins=1)

# Using BytesIO for testing
commands = BytesIO(b"0,100\n2,200\n3,150\nEND\n")
ctrlaer.listen(commands)

# Or using stdin for serial communication
# ctrlaer.listen(stdin)
```

**Example with multiple pins:**
```python
from io import BytesIO

# Create controller for 3 pins (GP0, GP1, GP2)
ctrlaer = CtrlAer(sm_number=0, base_pin=0, n_pins=3)

# Command for 3 pins: binary pattern 0b011100 (decimal 28)
# Pin 0 (rightmost 2 bits): 00 = OFF
# Pin 1 (middle 2 bits): 11 = HIGH
# Pin 2 (leftmost 2 bits): 01 = PULSE01
commands = BytesIO(b"28,250\n63,100\n57,100\nEND\n")
# 28,250 (0b011100): PULSE01-HIGH-OFF for 250 ms
# 63,100 (0b111111): HIGH-HIGH-HIGH for 100 ms
# 57,80 (0b110101): HIGH-PULSE10-PULSE01 for 80 ms
ctrlaer.listen(commands)
```

##### `ticks(time)`

Converts milliseconds to timer ticks.

**Parameters:**
- `time` (int): Time in milliseconds

**Returns:**
- Number of timer ticks (where 1 tick = one square wave pulse period = 10 PIO clock cycles)

**Notes:**
- The conversion formula is `ticks = time * freq / 1000`
- At 100 kHz, 100 ms = 10000 ticks

##### `is_full()`

Checks if the PIO FIFO is full.

**Returns:**
- Boolean indicating FIFO status

##### `is_empty()`

Checks if the PIO FIFO is empty.

**Returns:**
- Boolean indicating FIFO status

## Program structure

### Generator programs

Programs are Python generators that yield `(state, duration)` tuples:
- `state`: Pin state constant (HIGH, LOW, PULSE10, etc.)
- `duration`: Time to maintain this state in milliseconds

The duration is independent of the square wave frequency set in the CtrlAer constructor.

### PIO implementation

The library uses a PIO program that:
1. Pulls pin states from FIFO
2. Sets output pins
3. Pulls duration from FIFO
4. Sets outputs to one or the other state for the specified duration

For square wave states (PULSE10/PULSE01), the output toggles at the frequency specified in the CtrlAer constructor.

**Implementation details:**
- The PIO state machine runs at 10 times the square wave frequency (`freq * 2 * 5`)
- Each square wave pulse period equals 10 PIO clock cycles (1 tick)
- Duration is measured in ticks (square wave pulse periods)
- The `use_ms` parameter in `run()` determines whether durations are interpreted as milliseconds or raw ticks