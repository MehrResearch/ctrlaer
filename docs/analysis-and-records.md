# Preview an experiment and save its results

A preview helps you check a sequence before connecting equipment: which output
starts first, how long it stays on, and whether the pauses are where you expect.
You can make previews on your computer without a Pico. CtrlAer reads the same
experiment function that you would run on the board.

## Draw a waveform

This function sweeps a square wave between 90 and 120 kHz for 20 ms, then pauses
for 100 ms. Each journey between the two frequencies takes 2 ms, so the active
part contains five complete up-and-down sweeps.

```python
from ctrlaer import sweep, OFF, compile_program
from ctrlaer_tools import render_svg

def experiment(duration_ms=20, sweep_ms=2):
    yield sweep(90_000, 120_000, sweep_ms=sweep_ms), duration_ms
    yield OFF, 100

program = compile_program(experiment, clock_hz=125_000_000)
print(program.summary())
with open("preview.svg", "w") as stream:
    stream.write(render_svg(program))
```

Open `preview.svg` in a browser. To see the individual HIGH and LOW intervals,
use `render_svg(program, window_ms=0.1)` to display the first 100 µs. A dense
waveform viewed over its whole duration looks like a solid band.

The summary reports the duration, number of rising edges on each channel and
required memory. It also reports timing changes caused by rounding to the
Pico's clock. Pass the board's `machine.freq()` as `clock_hz` when you intend
to run exactly the program you previewed.

An input wait has no known duration until the input arrives. Previews mark its
position in the sequence and show the output without adding an imaginary wait.
For a function that loops forever, set `window_ms` in `compile_program()`.

## Preview a repeating experiment

You do not need to run a repeating function to its end to preview it. For
example, reuse the cycle above in a loop and calculate its first 350 ms:

```python
def repeating_experiment():
    while True:
        yield from experiment()

program = compile_program(repeating_experiment, window_ms=350,
                          clock_hz=125_000_000)
with open("repeating-preview.svg", "w") as stream:
    stream.write(render_svg(program))
```

`window_ms` is the duration shown in the preview, not a delay while calculating
it. The function stops being read when the window is filled. This produces a
reference for that part of the experiment; it does not limit how long a buffered
run can continue on the Pico.

## Compare settings

`parameter_scan()` creates a preview for every combination of settings. It does
not run the physical experiments. This example compares three output durations
and three sweep speeds, producing nine programs:

```python
from ctrlaer_tools import parameter_scan

for index, settings, program in parameter_scan(
        experiment,
        {"duration_ms": [10, 20, 40], "sweep_ms": [1, 2, 5]},
        clock_hz=125_000_000):
    with open("preview-{}.svg".format(index), "w") as stream:
        stream.write(render_svg(program))
    print(index, settings, program.duration_ms)
```

Each combination calls the function again with those arguments. Ordinary Python
operations in the function still happen, including changes to global variables,
sensor reads and file writes. Keep that in mind when previewing a function that
also interacts with equipment.

## Work from the command line

The checkout includes an example function and a parameter grid:

```sh
python -m ctrlaer_tools preview examples/adiabatic.py:experiment \
    --svg preview.svg --compiled prepared.json
python -m ctrlaer_tools scan examples/adiabatic.py:experiment \
    --axes examples/adiabatic_scan.json --out-dir scan
```

`--parameters` reads function arguments from a JSON file. `--clock-hz`,
`--freq` and `--n-pins` describe the intended controller. `--window-ms` limits
the part of a repeating experiment to preview.

## Save what you ran

After an experiment, a record can keep the prepared signal, settings, output
measurements and your own results together. For a finite run:

```python
from ctrlaer_records import save_record, load_record

# Run this after run.wait() has returned.
save_record("experiment.json", run.program, run=run,
            results={"sensor_reading": 18420},
            metadata={"sample": "A", "replicate": 1})

# You can also load the file later on your computer.
record = load_record("experiment.json")
```

If you [captured the output](digital-capture.md), add `trace=trace` to include
the measurements. The record contains the exact output data and checksums,
along with the board and MicroPython information when saved on the Pico.
Save after output has stopped: writing a large file takes Python's time and
could interrupt production of a buffered waveform.

A repeating experiment has no complete stored waveform. To save a reference
for it, compile and record a finite window, and describe that window in your
metadata. The reference covers that window, rather than future instructions
the function has not produced yet.
