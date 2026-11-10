# Prepare and replay an experiment

Preparing an experiment lets you check its complete output before starting it.
You can then run the same sequence several times without recalculating it. This
is useful for repeated measurements where the timing should be identical.

This example produces a 40 kHz square wave for 2 ms, followed by an 8 ms pause.
Connect GP0 to a compatible driver input and connect the grounds, as shown in
the [wiring guide](wiring.md).

```python
from ctrlaer import CtrlAer, ON, OFF

def experiment():
    yield ON, 2
    yield OFF, 8

controller = CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=40_000)
try:
    run = controller.prepare(experiment())
    for repetition in range(3):
        if repetition:
            run.rearm()
        run.start().wait(timeout_ms=1000)
        print("Finished repetition", repetition + 1)
finally:
    controller.close()
```

`prepare()` reads the function's instructions, calculates the output changes and
checks that the timing is possible. It leaves the output LOW until `start()`.
`wait()` returns when the experiment finishes. `rearm()` makes a finished or
aborted run ready to start again; it must still be open. `close()` stops the run,
leaves its outputs at their final levels, and releases the PIO hardware.

The final output level is LOW unless you set `final_mask`. For several channels,
each bit of that integer selects one channel's final level. For example,
`final_mask=0b01` leaves the first channel HIGH and the second LOW.

## Check the sequence before connecting equipment

You can calculate and inspect a program on your computer, then pass the result
to `controller.prepare()` on the Pico. The [preview guide](analysis-and-records.md)
explains how to draw it and save a record.

```python
from ctrlaer import compile_program

program = compile_program(experiment, freq=40_000, clock_hz=125_000_000)
print(program.summary())
```

Use the target Pico's `machine.freq()` for `clock_hz`; the desktop default is
125 MHz. Changing that clock requires recalculating the program. Frequencies
specified by `square_wave()` or `sweep()` belong to those instructions; `freq`
sets the frequency of `ON` and the other integer commands.

## Account for memory

A prepared program stores each output change. Long square waves therefore need
more memory than long constant levels. A 20 ms square wave at 100 kHz needs
about 32 KiB for its output changes, plus a small amount for control information.
The [performance guide](performance.md) compares practical examples.

The complete sequence must fit in memory and have an end. If your function loops
forever, give `compile_program()` a `window_ms` to preview just its beginning,
or use [buffering](execution-modes.md#what-buffering-does) to run it.
A preview window ends at the requested time; it cannot be resumed as the next
part of the experiment. `max_words` and `max_segments` reject programs that
exceed your chosen storage or instruction limit.

## Messages, pauses and other Python code

CtrlAer supplies `sleep`, `print` and `wait_for` so that a pause, message or input
wait can belong to the experiment's sequence. For example:

```python
from ctrlaer import ON, OFF, sleep, print as report

def experiment():
    yield ON, 2
    yield OFF, 1
    report("Illumination finished")
    sleep(0.1)  # Keep the output LOW for another 100 ms.
```

The message is displayed when the experiment reaches that point, with some
delay while Python handles the notification. It is useful for following
progress, but console messages do not measure the precise time a pin changed.
`sleep()` holds the last HIGH or LOW level; a preceding square wave does not
keep oscillating during the pause.

Other Python code runs while CtrlAer reads the function. For example, a sensor
read in a prepared function happens during preparation. If you want a sensor
read between two physical actions, use the
[measurement example](examples.md#use-python-measurements-between-commands).
Preparing or previewing a function can also change global variables or write
files if the function contains those operations.
