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

`python3 tools/board.py --port /dev/cu.usbmodem3101 --deploy
tests/hardware/prepared_lifecycle.py` deploys the current modules and runs a check.
The tool resets the interpreter and overwrites only `/lib/ctrlaer*.py` modules.
It requires POSIX USB serial access and does not flash firmware.

Prepared-runtime check on the same board: completion notification after 22136 us
for a 21000 us timeline, successful rearming, abort to HIGH, wait-timeout to LOW,
and an injected stopped-DMA feeder correctly reported as an underrun.
