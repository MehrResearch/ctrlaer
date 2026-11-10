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

The following measurements were made on 10 November 2026 with MicroPython 1.29.0
on a Pimoroni Tiny 2040 2MB, and Pimoroni MicroPython 1.29.0-2 on a Pico Plus 2.
The clocks were 125 MHz and 150 MHz respectively; the Pico's heap included PSRAM.
These are measurements of this workload, rather than a latency limit for
arbitrary Python generators.

The cold workload emits a GPIO marker **before importing CtrlAer**, then starts
a 90–120 kHz chirp, testing both 2 ms and default 20 ms sweep legs. The other
board captures the marker and first waveform edge at 250 kS/s. Each measurement starts from a soft reset;
the sender's CtrlAer modules have not already been imported. This includes
imports, command construction, compilation, validation, PIO/DMA allocation and
FIFO prefill. It excludes board boot, USB transport and external trigger waiting.
Both boards' LEDs remain off during these measurements.

Finite and eager commands last 20 ms. The buffered command lasts 60 seconds,
establishing that startup does not require compiling that entire duration; the
test aborts it shortly after the first output. The figures measure **first
output**, rather than the return of a blocking call that waits for its waveform.

| Sweep leg | Cold marker-to-first-output latency | Tiny 2040 | Pico Plus 2 |
|---|---|---:|---:|
| 2 ms | Finite prepared | 366 ms | 291 ms |
| 2 ms | Buffered | 421 ms | 354 ms |
| 2 ms | Eager | 415 ms | 351 ms |
| 20 ms | Finite prepared | 387 ms | 304 ms |
| 20 ms | Buffered | 442 ms | 367 ms |
| 20 ms | Eager | 437 ms | 364 ms |

All twelve observations are below 500 ms. They are observations of these
workloads, not a universal bound on arbitrary generator construction or
preparation. Precompiled modules remove source parsing/native compilation from
cold startup. Deferred eager setup skips an empty fragment, and phase-aligned
triangular chirps prepare only one sweep leg of their symmetric cache.

### Raw kernel throughput

These separate warm measurements exclude the runtime and packet packing:

| Measurement | Tiny 2040 | Pico Plus 2 |
|---|---:|---:|
| Warm 108.05 kHz pulse edge generation | 0.79 million edges/s | 1.31 million edges/s |
| Warm repeating 90–120 kHz chirp edge generation | 0.83 million edges/s | 1.22 million edges/s |
| Warm 90.001–120 kHz chirp edge generation | 0.35 million edges/s | 0.56 million edges/s |

The warm test reuses a 1024-word output array for 128 fills. It measures edge
generation, **excluding packet packing, generator execution, refill scheduling
and other application work**. A 90–120 kHz chirp needs 210,000 edges/s on average.
Changing the lower frequency by 1 Hz prevents the short exact phase repeat, so
the last row uses the inverse-phase lookup. Its native packet path also removes
per-edge Python work. Raw edge throughput alone is insufficient: the sustained
checks below include packing, refills, scheduling and garbage collection.

The exact chirp cache is enabled only when both frequency trajectory and
accumulated carrier phase repeat after an integer number of system-clock ticks.
It covers at most 10,000 edge intervals, independent of ON duration. Intervals
use two bytes each, or a lossless one-byte delta from a fixed base when the
interval range fits. A phase-aligned 50% chirp whose half cycles align at each
reversal stores one sweep leg and reads its intervals backward for the return.
The default 90–120 kHz command therefore retains 420 bytes with 2 ms legs, or
4200 bytes with 20 ms legs. Other duty/phase/dead-time settings can require the
full table, up to 20,000 bytes.

Non-repeating phase uses a bounded inverse-phase lookup rather than restarting
an approximate carrier cycle. It stores two sweep-leg tables of Q4 clock times
and preserves the full fractional carrier phase across reversals and chunks.
The native lookup path reports a one-system-clock edge bound, tested against
the independent analytic model. The 90.001–120 kHz example uses 6736 bytes with
2 ms legs and 16,816 bytes with 20 ms legs; the maximum table is 32,784 bytes.
Repeated buffered shots with unchanged settings reset and reuse the native
cursor and its table, avoiding repeated setup arithmetic and allocations. Each
new shot starts its own phase/sweep at zero. Pulse generation uses an
integer/fractional timing accumulator. Unsupported native parameter ranges
use the analytic compiler, preserving functionality with a higher cost.

## Reproduce the measurements

Install this checkout first. To measure cold startup independently over the
[GPIO6/GND link](wiring.md#two-boards-send-and-verify-in-either-direction):

```sh
python3 tools/start_latency.py \
  --tiny-port /dev/cu.usbmodem3101 --pico-port /dev/cu.usbmodem101 \
  --gpio 6 --mode all --sample-hz 250000 --out-dir work/start-latency
```

The tool stores JSON results and a VCD trace for each mode/direction. It reports
both physical first-output latency and software timing. These measurements use
4 µs samples; select `--sample-hz 1000000` for 1 µs resolution. Add `--sweep-ms 20`
to test the default sweep. The two boards have independent clocks.

For the separate warm kernel benchmark, run the following from the repository
root, substituting the serial port for the selected board:

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
  --pico-port /dev/cu.usbmodem101 --gpio 6 --no-leds --out-dir work/cross-board
```

The generic-chirp check adds `--start-hz 90001`. It observed 2338 expected rises
per direction, including the start marker, with zero mismatches. Host and device
produced identical packed programs. After fitting the independent clocks (about
9 ppm apart), maximum residuals were 50/52 ns at 10 MS/s capture.

With `--mode buffered --chirp-ms 100 --sample-hz 4000000`, the generic chirp
produced 21,476 transitions per direction, with zero mismatches and 127/128 ns
maximum fitted residuals. Adding `--sweep-ms 20` while keeping the default
90–120 kHz frequencies checks the compact symmetric cache: 21,474 transitions,
zero mismatches, 127/128 ns residuals. These fits check waveform shape and
scheduling; they do not assert a shared clock.

## Sustained buffered production

The chained-DMA runtime was exercised on both boards with these workloads:

| Workload | Duration per board | Result |
|---|---:|---|
| Continuous 108.05 kHz pulse, one long yielded command | At least 30 s | No underrun |
| Continuous 90–120 kHz chirp, 2 ms per leg, one long command | At least 30 s | No underrun |
| Repeated 20 ms 108.05 kHz bursts / 20 ms OFF | At least 30 s | No underrun |
| Repeated 20 ms 90–120 kHz chirps / 20 ms OFF | At least 30 s | No underrun |
| Continuous 90–120 kHz chirp, default 20 ms leg | At least 30 s | No underrun |
| Continuous 90.001–120 kHz chirp, 2 ms leg | At least 30 s | No underrun |
| Repeated 20 ms 90.001–120 kHz chirps / 20 ms OFF | At least 30 s | No underrun |

Each workload crosses thousands of buffer boundaries. The long commands cross
the native cursor's local-coordinate rollover and Python's small-integer range.
Collections remain enabled. Scheduling collection earlier, shortly after a
fresh DMA handoff, avoids an additional automatic collection in the middle of
burst production on the Tiny. Stable cached/lookup commands collect less
frequently than burst boundaries, where generator advances and validation
create more garbage. The default pool is 96 KiB, with four transmit DMA
channels and a prepared spare. Dense pulses, cached chirps and generic lookup
chirps each use a direct native packet writer. Sparse chunks transfer their
actual word counts without full-buffer padding.
The default pool's unused alignment space holds the compiler's edge scratch
and two disjoint event-receive rings, leaving more of the Tiny's heap available
for the application and capture. Hardware checks verify that these regions do
not overlap any transmit ring or each other.

`tests/hardware/buffered_stress.py` reproduces all seven workloads for thirty
seconds each. Allow about four minutes and pass `--timeout 300` to
`tools/board.py`. Change `SOAK_SECONDS` for a different duration. Use the same
`tools/board.py --port ...` command form as the kernel benchmark.
It reads heap statistics only after stopping output. Separate lifecycle checks
cover gate timeouts, repeated triggers, abort/rearm, final masks and deliberately
slow producers; an intentional deadline miss must raise `UnderrunError`.

These results establish sustained production for the listed workloads. Generic
chirps and general rich `mux()` merges use more computation, and a producer
that blocks Python for too long can still underrun. Increasing buffering absorbs
pauses but does not repair an insufficient average production rate. Use finite
preparation for a reviewable schedule that fits memory, or eager execution when
Python gaps between commands are acceptable. See [execution modes](execution-modes.md).

The full measurements, runtime snapshots and installed-source hashes are saved
in `tests/hardware/validation-2026-11-10.json` in this checkout.
