# Generator analysis, scans and records

Analysis runs the same generator API as execution. It interprets CtrlAer-owned
effects and compiles exact output edges; it does not provide a second authoring
language. Ordinary Python side effects still happen.

```python
from ctrlaer import chirp, OFF, print, compile_program
from ctrlaer_tools import render_svg, parameter_scan

def experiment(pulse_ms=20, sweep_ms=2):
    print("shot", pulse_ms, sweep_ms)
    yield chirp(90000, 120000, sweep_ms=sweep_ms), pulse_ms
    yield OFF, 100

program = compile_program(experiment,
                          parameters={"pulse_ms": 20, "sweep_ms": 2})
print(program.summary())
with open("preview.svg", "w") as stream:
    stream.write(render_svg(program))
```

The [sweep example](examples.md#sweep-repeatedly-within-one-pulse) shows the same
ON/rest waveform and its frequency trajectory. This analysis code runs on
desktop Python with no physical connections. On a board, save large previews or
records after playback stops; file I/O shares the interpreter with production.

The summary includes channel names, pulse counts, nominal duration, requested
duration, duration/edge quantisation, control overhead, effect annotations, gates
and buffer size. An infinite generator needs `window_ms`; size/complexity guards
raise errors when exceeded. `render_svg` consumes the compiled buffer without
advancing the generator again. Limit its view/edge count for dense waveforms.

```python
for index, parameters, compiled in parameter_scan(
        experiment,
        {"pulse_ms": [10, 20, 40], "sweep_ms": [1, 2, 5]}):
    # Each trial calls experiment(**parameters) to obtain a fresh generator.
    print(index, parameters, compiled.pulse_counts)
```

Axes are enumerated in sorted-name order, preserving the supplied value order.
`fixed` supplies other ordinary keyword arguments. `max_trials` prevents accidental
combinatorial explosion. Reproducibility of arbitrary global state/randomness
remains the caller's responsibility; saving exact compiled buffers preserves what
was actually prepared even when Python state is dynamic.

## Desktop command line

```sh
python -m ctrlaer_tools preview examples/adiabatic.py:experiment \
    --svg preview.svg --compiled prepared.json
python -m ctrlaer_tools scan examples/adiabatic.py:experiment \
    --axes examples/adiabatic_scan.json --out-dir scan
```

`module:function` also works. `--parameters` reads normal keyword arguments from
a JSON file. `--n-pins`, `--freq` and `--clock-hz` match the controller setup.
`--window-ms` bounds compilation of an infinite generator. Exact compiled JSON
artifacts can be previewed directly and loaded for execution; they are saved
output, not an alternative way to author experiments. Importing/running a Python
experiment is explicit and can have ordinary Python side effects.

## Experiment records

```python
from ctrlaer_records import save_record, load_record

# After run.wait() and capture.wait():
save_record("shot.json", run.program, run=run, trace=trace,
            results={"comparison": trace.compare(run.program)},
            metadata={"sample": "A", "replicate": 1})
record = load_record("shot.json")  # Load large records on the desktop.
```

A versioned record contains supplied named parameters, exact prepared words,
captured words, device/MicroPython version and clock, execution status, observed
effects/gates, result data and user metadata. Buffers use little-endian base64
with SHA-256 checks; corrupt buffers and inconsistent executable metadata are
rejected. Writing streams small chunks, then atomically renames the completed
file. Offline records are explicitly labelled offline rather than inventing a
device firmware version. Large records are best loaded on the desktop.

This example uses a finite prepared run, whose `run.program` contains all its
words. A buffered run exposes a timing/output header instead. Analyse a separate
finite window when recording a reference for an indefinite program, and label
that window explicitly; it does not describe the unconsumed future.

`Trace.timestamps` provides hardware-sampled relative input/output timestamps.
Gate/print timestamps in runtime status are service observations, not precise
hardware edge timestamps. CSV and VCD exports can accompany the combined record.
