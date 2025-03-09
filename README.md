# CtrlAer

[![DOI](https://zenodo.org/badge/690546753.svg)](https://doi.org/10.5281/zenodo.14763862)

A MicroPython library for programmable real-time execution of scientific experiments on Raspberry Pi Pico. CtrlAer enables synchronized operation of multiple hardware devices through a simple, yet powerful domain-specific language.

## Overview

CtrlAer is designed to lower the barrier to entry for scientists and engineers looking to undertake experiments involving synchronized activation of multiple devices. It can generate arbitrarily complex finite or infinite signal sequences on up to 16 synchronized parallel channels at up to 10 MHz.

### Key Features

- **High-Precision Timing**: Generate signals with microsecond precision
- **Multiple Synchronized Outputs**: Control up to 16 parallel channels
- **Flexible Programming**: Create complex signal patterns using Python generators
- **Real-Time Performance**: Direct hardware execution via RP2040's PIO
- **Wide Hardware Support**: Works with all RP2040/RP2350-based boards

### Target Applications

- Motor control (including stepper motors)
- Solid state and mechanical relays
- Solenoid valves
- Light sources
- High-frequency mechanical transducers
- Ultrasonic emitters
- Piezoelectric actuators
- Vibrating mesh atomizers (VMAs)

## Quick Start

1. Install MicroPython on your Raspberry Pi Pico
2. Copy `ctrlaer.py` to your Pico
3. Create your control program:

```python
from ctrlaer import mux, HIGH, LOW, CtrlAer

def phase1(n_steps):
    for i in range(n_steps):
        yield HIGH, 50  # ON for 50ms
        yield LOW, 50   # OFF for 50ms

def phase2(n_steps):
    for i in range(n_steps):
        yield LOW, 50   # Start OFF
        yield HIGH, 50  # ON for 50ms

# Combine both phases
progs = [phase1(10), phase2(10)]
prog = mux(progs)

# Create controller and run
controller = CtrlAer(sm_number=0, base_pin=0, n_pins=2, freq=10000)
controller.run(prog)
```

## Documentation

Full documentation is available at [https://mehrresearch.github.io/ctrlaer](https://mehrresearch.github.io/ctrlaer), including:

- Getting Started Guide
- API Reference
- Example Applications
- Hardware Setup Instructions

## Hardware Compatibility

CtrlAer works with any RP2040/RP2350-based development board, including:
- Raspberry Pi Pico / Pico W
- Raspberry Pi Pico 2
- AdaFruit RP2040-based boards
- SparkFun RP2040-based boards
- Seeed Studio RP2040-based boards
- Pimoroni RP2040-based boards

## Academic Citation

If you use CtrlAer in your research, please cite:

```bibtex
@article{mehr2025ctrlaer,
    title={Programmable real-time execution of scientific experiments using a domain specific language for the Raspberry Pi Pico},
    author={Mehr, S. Hessam M.},
    journal={},
    year={2025},
    publisher={}
}
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.

