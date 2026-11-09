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
    - Maximum frequency for square pulse generation is processor clock frequency / 14, since 14 PIO instructions are used to generate a square wave.
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

If you use CtrlAer in a scientific publication, please cite the [associated paper](https://doi.org/10.1016/j.softx.2025.102175) (DOI: 10.1016/j.softx.2025.102175):

```bibtex
@article{Mehr2025CtrlAer,
title = {CtrlAer: Programmable real-time execution of scientific experiments using a domain specific language for the Raspberry Pi Pico/Pico 2},
journal = {SoftwareX},
volume = {30},
pages = {102175},
year = {2025},
issn = {2352-7110},
doi = {https://doi.org/10.1016/j.softx.2025.102175},
url = {https://www.sciencedirect.com/science/article/pii/S2352711025001426},
author = {S.Hessam M. Mehr},
keywords = {Raspberry Pi Pico, Automation, Signal generation, MicroPython, Microcontroller, Real-time, Domain-specific language},
abstract = {Automated laboratory experimentation is increasingly dependent on synchronized operation of a heterogeneous hardware setups according to arbitrarily complex user-defined timing, but there is a lack of accessible, vendor-neutral options for reliable generation of these control signals. We present, CtrlAer, a domain-specific language for describing activation signals on a synchronized parallel timeline via a simple syntax containing only a handful of primitives. Embedded within MicroPython, CtrlAer programs are directly executable on the widely available and inexpensive Raspberry Pi Pico/Pico 2 and the wide ecosystem of open hardware development boards built around the RP2040/RP2350 microcontrollers. CtrlAer allows arbitrarily long and complex control sequences to be generated on up to 16 fully synchronized parallel channels at up to 10.7 MHz on the RP2350 (8.9 MHz on the RP2040), scaling to the needs of scientific experiments in a variety of disciplines.}
}
```
