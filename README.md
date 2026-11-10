# CtrlAer

[![DOI](https://zenodo.org/badge/690546753.svg)](https://doi.org/10.5281/zenodo.14763862)

CtrlAer turns Python generators into precisely timed digital waveforms on
RP2040 and RP2350 boards. It is used for laboratory experiments involving
coordinated drivers, atomisers, valves, illumination and other logic-controlled
hardware.

Programs yield `(command, duration)` pairs. The same vocabulary supports finite
prepared playback, indefinite buffered operation with hardware triggers, eager
execution with Python measurements between commands, and desktop analysis.
`mux()` combines up to 16 consecutive output channels; `Group` coordinates
independent state machines. Commands include fixed carriers, duty/phase control,
complementary dead time and continuous-phase triangular frequency sweeps.

## Install or upgrade

With [uv](https://docs.astral.sh/uv/getting-started/installation/), from this checkout:

```sh
uv run tools/install.py
```

The script enumerates USB serial devices and asks you to select one. It compiles
native `.mpy` modules for the selected chip, uploads and verifies them, and keeps
the previous library for rollback. Use `--device PORT` or `--device USB_SERIAL`
for explicit selection. Repeat after updating the checkout to upgrade. Requires
board-specific MicroPython 1.29.0 or newer; user application files are retained.
See the [installation guide](docs/installation.md) for firmware, standalone use
and troubleshooting.

## First waveform

Run this on the board. GP6 produces three 2 ms bursts at 40 kHz, each followed by
8 ms LOW. Each burst contains 80 carrier periods. GP6 is a GPIO label, not
physical header position 6.

```python
from ctrlaer import CtrlAer, ON, OFF

def bursts():
    for _ in range(3):
        yield ON, 2
        yield OFF, 8

controller = CtrlAer(0, base_pin=6, n_pins=1, freq=40000)
try:
    controller.prepare(bursts()).start().wait(timeout_ms=1000)
finally:
    controller.close()
```

![Three 40 kHz bursts, with 8 ms LOW between them](docs/assets/first-bursts.svg)

Preparation consumes this finite generator before enabling output. The PIO/DMA
engine then plays the waveform, signals completion, and finishes LOW. No jumper
is needed to execute it. To observe it, connect a digital input to GP6 and common
GND. See [physical pins and wiring](docs/wiring.md) before connecting a driver.

## Choose execution timing

| Need | Entry point |
|---|---|
| Validate, preview and replay a complete finite schedule | `controller.prepare(program)` |
| Run indefinitely with hardware gates and bounded waveform storage | `controller.prepare(factory, mode="buffered")` |
| Complete each command before resuming ordinary Python | `controller.run(program, mode="eager")` |
| Keep the original integer-command FIFO API | `controller.run(program)` |

Buffered operation keeps producing while PIO plays earlier chunks; production
must meet its deadlines. Eager operation deliberately permits Python gaps
between commands. Ordinary Python side effects happen whenever the generator
advances; use CtrlAer's `sleep`, `print` and `wait_for` for managed playback
operations. The original FIFO `block` argument waits for queue space; call
`controller.block()` for actual completion.

## Documentation and validation

The [full documentation](https://mehrresearch.github.io/ctrlaer/) and this
checkout's guides cover:

- [Getting started](docs/getting-started.md) and [worked examples](docs/examples.md), with waveform plots.
- [Execution modes](docs/execution-modes.md), [hardware triggers and groups](docs/triggers-and-groups.md).
- [Digital capture](docs/digital-capture.md), [previews, parameter scans and records](docs/analysis-and-records.md).
- [API reference](docs/api-reference.md), generated from actual signatures and docstrings.
- [Performance measurements](docs/performance.md) and [reproducible hardware checks](tests/hardware/README.md).

Cold first-output latency and sustained production have been measured on a Tiny
2040 and Pico Plus 2, including independent bidirectional capture over GPIO6/GND.
Performance depends on the waveform, chip, available memory and Python work;
see the measured envelope in the performance guide.

## Academic Citation

If you use CtrlAer in your research, please cite:

```bibtex
@article{mehr2025ctrlaer,
    title={CtrlAer: Programmable real-time execution of scientific experiments using a domain specific language for the Raspberry Pi Pico/Pico 2},
    author={Mehr, S. Hessam M.},
    journal={SoftwareX},
    volume={30},
    pages={102175},
    year={2025},
    publisher={Elsevier},
    doi={10.1016/j.softx.2025.102175}
}
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
