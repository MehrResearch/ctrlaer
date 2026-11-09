# Startup and waveform production

CtrlAer has two distinct costs: preparing the waveform before starting, and
producing more waveform data while a buffered run is active. A fast start alone
does not establish that a producer can sustain continuous playback.

## Install precompiled modules

Use `uv run tools/install.py` to install architecture-specific `.mpy` files.
MicroPython can import these without parsing the Python source and compiling the
native kernels on every soft reset. The native arithmetic is included in these
files; no custom firmware is required.

Uploading `.py` copies alongside the installed `.mpy` files can make MicroPython
load the source instead. The installer moves previous library files out of the
import path and checks which files were actually imported before reporting
success. See [installation](installation.md).

## Measurements on the two development boards

The following measurements were made on 9 November 2026 with MicroPython 1.29.0
on a Pimoroni Tiny 2040 2MB, and Pimoroni MicroPython 1.29.0-2 on a Pico Plus 2.
The clocks were 125 MHz and 150 MHz respectively; the Pico's heap included PSRAM.
These are measurements of this workload, rather than a latency limit for
arbitrary Python generators.

The cold workload starts timing **before importing CtrlAer**, prepares a 20 ms
90–120 kHz chirp with 2 ms per sweep leg, then enables playback. It includes
imports, command construction, compilation, validation, PIO/DMA allocation and
FIFO prefill. It excludes board boot, USB transport and external trigger waiting.
`start()` returns after enabling the PIO, so the measured time bounds the first
scheduled output from above.

| Measurement | Tiny 2040 | Pico Plus 2 |
|---|---:|---:|
| Cold import + prepare + start | 361 ms | 285 ms |
| Warm 108.05 kHz pulse edge generation | 0.78 million edges/s | 1.32 million edges/s |
| Warm repeating 90–120 kHz chirp edge generation | 1.23 million edges/s | 1.91 million edges/s |
| Warm 90.001–120 kHz chirp edge generation | 0.20 million edges/s | 0.31 million edges/s |

The warm test reuses a 1024-word output array for 128 fills. It measures edge
generation, **excluding packet packing, generator execution, refill scheduling
and other application work**. A 90–120 kHz chirp needs 210,000 edges/s on average.
The last row shows why the production rate must be checked for the actual
workload: changing the lower frequency by 1 Hz prevents the short exact repeat
used by the cached path, and this uncached example cannot sustain playback on
the Tiny even before packing costs.

The chirp cache is enabled only when both frequency trajectory and accumulated
carrier phase repeat. It retains at most two arrays of 2048 edges each, independent
of pulse duration. The common 2 ms-leg example uses 6720 bytes. Pulse generation
uses an integer/fractional timing accumulator. Unsupported native parameter
ranges use the analytic compiler, preserving functionality with a higher cost.

## Reproduce the measurements

Install this checkout first. Then run the following from the repository root,
substituting the serial port for the selected board:

```sh
python3 tools/board.py --port /dev/cu.usbmodem3101 tests/hardware/production_rate.py
```

The script soft-resets the selected board, emits the test waveform on GPIO8, and
returns GPIO8 to an input. It needs no external wiring. The JSON output includes
the clock, board identity, cold latency, first-fill cost, warm throughput and
retained heap growth after garbage collection. Small retained-allocation changes
include the measurement's own Python bookkeeping; this is not a heap leak test.

To check waveform accuracy independently, connect GPIO6 between two boards and
connect their grounds. Then run:

```sh
python3 tools/cross_board.py \
  --tiny-port /dev/cu.usbmodem3101 \
  --pico-port /dev/cu.usbmodem101 --gpio 6 --out-dir work/cross-board
```

The latest bidirectional test observed 2337 rising edges per direction with zero
mismatches. The host and device produced identical packed programs. After fitting
the independent clocks (about 9 ppm apart), maximum edge residuals were 52 ns and
53 ns at 10 MS/s capture. That fitted result checks waveform shape and scheduling;
it does not assert that the boards share a clock.
