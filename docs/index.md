# CtrlAer

A MicroPython library for precise binary signal generation on Raspberry Pi Pico/Pico 2, utilizing the RP2040/2350 PIO (Programmable I/O) system.

## Overview

CtrlAer provides a high-level interface for controlling multiple output pins with precise timing using the RP2040/2350's PIO state machines. It's particularly useful for applications requiring:

- Synchronized triggering of multiple devices with specific timing
- Multiple synchronized square wave outputs with configurable frequency and phase
- Low and high-frequency signal generation, including square waves, pulses, and constant signals
- Arbitrarily complex/long dynamically generated signal patterns

## Key features

- **Precise timing control**: Generate signals with exact timing at any integer division of the microcontroller's base frequency
    - Maximum frequency for square pulse generation is processor clock frequency / 10, since 10 PIO instructions are used to generate a square wave.
- **Multiple outputs**: Control multiple pins simultaneously with perfect synchronization
- **Flexible programming**: Create complex signal patterns using Python generators and a handful of CtrlAer primitives
- **High performance**: Utilizes RP2040/2350's PIO for reliable timing
- **Easy to use**: Simple Python API for complex timing control

## Usage
Simply download the latest release .zip file from the [releases](https://github.com/MehrResearch/ctrlaer/releases) page and copy the contents to your Raspberry Pi Pico under the `lib` folder.

## Quick example

Here's a simple example that alternates two pins:

```python
from ctrlaer import mux, ON, OFF, CtrlAer
# ON: Square wave with 50% duty cycle at frequency `freq`
# (see `CtrlAer` constructor); OFF and LOW are synonymous.

def phase1(n_steps):
    for i in range(n_steps):
        yield ON, 50  # ON for 50ms
        yield OFF, 50   # OFF for 50ms

def phase2(n_steps):
    for i in range(n_steps):
        yield OFF, 50   # OFF for 50ms
        yield ON, 50  # ON for 50ms

# Combine the two phases
progs = [phase1(10), phase2(10)]
prog = mux(progs)

# Create a CtrlAer instance and run the program
controller = CtrlAer(sm_number=0, base_pin=0, n_pins=len(progs), freq=10000)
controller.run(prog)
```

Check out the [Getting Started](getting-started.md) guide for more detailed information. 

## Citing CtrlAer

If you use CtrlAer in a scientific publication, please cite the following paper:

```bibtex
@article{mehr2025ctrlaer,
    title={Programmable real-time execution of scientific experiments using a domain specific language for the Raspberry Pi Pico},
    author={Mehr, S. Hessam M.},
    journal={},
    year={2025},
    publisher={}
}
```
