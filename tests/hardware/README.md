# Hardware checks

The checks in this directory are MicroPython scripts, not CPython unit tests.
They use an otherwise idle Pimoroni Tiny 2040. GP8/GP9 are not connected to the
Tiny 2040 headers or its LED; do not reuse this pin choice on another board without
checking its wiring. Tests always stop their own state machines in cleanup.

Initial baseline check on 2026-11-09: RP2040 at 125 MHz, MicroPython 1.29.0.
The corrected streaming `block()` returned after 201665 us for a requested
200000 us command; before the correction it returned after 610 us. A simultaneous
PIO/DMA probe confirmed signal generation remained operational.

The internal probe uses the same oscillator as the output. These checks establish
relative digital timing, not independent absolute frequency calibration.
