# Running an experiment

An experiment can repeat a sequence for as long as needed, including while it
waits for external triggers. Use a `while True` loop to describe that repetition.
The examples below start with a repeating square wave, then add a trigger or a
measurement.

## Repeat until you stop the experiment

Suppose a device needs a 40 kHz square wave for 2 ms, followed by an 8 ms pause.
This program repeats that ten-millisecond cycle until you stop it:

```python
from ctrlaer import CtrlAer, ON, OFF
from machine import Pin

def experiment():
    while True:
        yield ON, 2
        yield OFF, 8

controller = CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=40_000)
try:
    controller.run(experiment())
finally:
    controller.close()
    Pin(0, Pin.OUT, value=0)
```

Connect GP0 to the device's 3.3 V-compatible control input and connect its ground
to Pico GND. On a Pico, GP0 is physical pin 1 and GND is pin 3. A motor or
transducer needs an appropriate power driver; the Pico supplies its control
signal.

To stop a script running at the MicroPython REPL, press **Ctrl-C**. The `finally`
block stops the PIO state machine and puts GP0 LOW. Without this cleanup,
interrupting Python can leave the hardware running or the output HIGH.

`run()` keeps reading the next instruction as space becomes available in the
PIO queue. An `ON` instruction describes an entire group of square-wave cycles,
so the program does not store every HIGH and LOW change. Repeating for an hour
uses the same small queue as repeating for a second.

## Wait for a trigger on every repetition

A detector or another instrument may determine when the next cycle starts.
Put `wait_for()` inside the loop so that every repetition waits for a new input.
This example uses GP6 as the trigger input and GP0 as the output:

```python
from ctrlaer import CtrlAer, square_wave, OFF, wait_for
from machine import Pin

Pin(6, Pin.IN, Pin.PULL_DOWN)

def shots():
    while True:
        wait_for(pin=6)
        yield square_wave(freq=40_000), 2
        yield OFF, 8

controller = CtrlAer(sm_number=0, base_pin=0, n_pins=1)
try:
    run = controller.prepare(shots, mode="buffered")
    run.start().wait()
finally:
    controller.close()
```

Connect a 3.3 V trigger output to GP6 and share its ground with the Pico. The
[trigger guide](triggers-and-groups.md) includes the instrument connection and
a button you can use to try it by hand.

The program waits for GP6 to be LOW and then HIGH. It produces the two-millisecond
square wave, stays LOW for eight milliseconds, then waits again. Trigger pulses
received during the output or pause are ignored. A wait with no timeout can last
indefinitely. Stop the experiment with Ctrl-C, or call `run.abort()` from your
application; both paths leave the prepared output LOW by default.

### What buffering does

Hardware input waits and adjustable signals such as `square_wave()` and `sweep()`
use instructions that specify when individual output changes occur. Storing
the complete output of a repeating experiment would eventually exhaust memory.
With `mode="buffered"`, CtrlAer calculates a few sections ahead while PIO plays
earlier ones, reusing the storage as it goes. The experiment can keep running
without its waveform storage growing over time.

An individual instruction can span many sections. For example, a ten-second
frequency sweep continues through those sections without restarting its
frequency or phase at each boundary.

Buffering reserves 96 KiB for waveform storage by default and uses six DMA
channels per controller. Python must prepare each new section before PIO needs
it. A long file write or a slow calculation can delay that work; if data runs
out, CtrlAer reports `UnderrunError` and stops at the final output levels. The
last level can remain in place until Python handles the error.

`buffer_words` changes the amount of storage. A smaller buffer saves memory but
leaves less time to recover from a Python pause. The
[performance examples](performance.md) explain the difference between holding
a level, producing square waves and sweeping frequency.

### When does code between instructions run?

Buffering reads the function ahead of the output. If you read a sensor after
`yield square_wave(...), 20`, that read may happen while CtrlAer is preparing
the signal, before the device has received it. The same applies to global
assignments, ordinary `print()` calls and other Python operations.

CtrlAer's `print`, `sleep` and `wait_for` put a message, pause or input wait in
the output sequence. They are useful for following progress or waiting for
another instrument. Use the next example when the result of a sensor read
must determine what happens after a physical action.

## Eager: measurements and ordinary Python in sequence

Suppose a light should illuminate a sample for 20 ms, then switch off before
you read a sensor. With `mode="eager"`, CtrlAer completes those timed actions
before reading the sensor. The
[light-and-sensor example](examples.md#use-python-measurements-between-commands)
includes the complete code and connections.

To repeat that measurement, put its instructions and sensor read inside a
`while True` loop. Each reading can then determine the next illumination time
or whether the experiment should stop. Python takes additional time to read
the sensor and prepare the next signal, so the time between repetitions varies.
The output stays at its last level during that work; an `OFF` instruction before
the measurement keeps the light off.

Eager execution uses the buffered implementation one instruction at a time,
with the same memory allocation. It waits inside `run()`, so `block=False` is
not supported. Use buffered output when the signal must follow a trigger at a
fixed delay; eager output also needs time for Python to prepare the next action
after a wait ends.

## Prepare a complete sequence for inspection or replay

For a sequence with a known end, `prepare()` can calculate and check all output
before starting. This lets you preview the signal and repeat exactly the same
stored data. It also removes the need for Python to prepare more data while
that sequence plays.

```python
from ctrlaer import CtrlAer, square_wave, OFF

def one_cycle():
    yield square_wave(freq=40_000), 2
    yield OFF, 8

controller = CtrlAer(sm_number=0, base_pin=0, n_pins=1)
try:
    run = controller.prepare(one_cycle())
    run.start().wait(timeout_ms=1000)
finally:
    controller.close()
```

Storage grows with the number of output changes. Use this for a complete
sequence that fits in memory, or prepare a short sequence once and `rearm()` it
between measurements. To preview an experiment that loops forever, give
`compile_program()` a `window_ms`; it calculates just that time window. See
[previews](analysis-and-records.md#preview-a-repeating-experiment) and
[preparing and replaying](prepared-execution.md) for examples.

## Why isn't buffering the default?

The `run()` path used in the first example generates fixed-frequency `ON`
signals with little memory and a compact PIO loop. Buffering supports hardware
waits, adjustable duty cycles and sweeps, but reserves more memory and transfer
channels and requires Python to keep producing data.

The current default keeps that small `run()` path for integer commands such
as `ON`, `OFF` and `HIGH`. For a sequence that ends, call `controller.block()`
after `run()` to wait for the last output to finish. The `block` argument to
`run()` controls waiting for queue space, which is a different operation.
The [controller reference](api/controller.md) covers nonblocking submission
and the arguments accepted by each entry point.
