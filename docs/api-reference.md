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
CtrlAer(sm_number, base_pin, n_pins, freq=FREQ)
```

**Parameters:**
- `sm_number` (int): PIO state machine number (0-7 on RP2040, 0-11 on RP2350)
- `base_pin` (int): First GPIO pin number
- `n_pins` (int): Number of pins to control
- `freq` (int, optional): Frequency in Hz for square wave generation. Defaults to 108,050 Hz.

**Notes:**
- State machines are grouped into PIO blocks (0-3, 4-7, 8-11)
- All state machines in the same PIO block share the same frequency
- Each PIO block can have its own frequency

#### Methods

##### run(prog, block=True)

Runs a program on the PIO state machine.

**Parameters:**
- `prog`: Generator program yielding `(state, duration)` tuples
- `block` (bool, optional): Whether to block until program completion. Defaults to True.

**Example:**
```python
controller = CtrlAer(0, 0, 2)
controller.run(my_program())
```

##### `block()`

Blocks until the current program completes (FIFO empty).

**Example:**
```python
controller.run(prog, block=False)
# Do other things
controller.block()  # Wait for completion
```

##### `set_freq(freq)`

Changes the square wave frequency for the PIO block containing this state machine.

**Parameters:**
- `freq` (int): New frequency in Hz

**Example:**
```python
controller.set_freq(20000)  # Set to 20kHz
```

##### `ticks(time)`

Converts milliseconds to timer ticks.

**Parameters:**
- `time` (int): Time in milliseconds

**Returns:**
- Number of timer ticks

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