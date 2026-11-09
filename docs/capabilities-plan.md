# Experiment capabilities implementation

This work preserves the existing generator DSL and adds an inspectable prepared
program path. Generators are the sole public authoring interface; no declarative
builder is retained. CtrlAer-owned sleep/print/wait calls use scoped interpreters;
arbitrary Python side effects remain the user's responsibility. No changes are
made to the untracked `examples/testing/` directory.

## Commit sequence and acceptance evidence

1. Correct finite multiplexing, zero durations, nonblocking submission and actual
   streaming completion. Verify host regressions and a long hardware command.
2. Add a portable, clock-based timeline compiler and a DMA runtime with explicit
   prepare/start/wait/abort, final outputs, bounded compilation and underrun
   reporting. Verify timing, terminal completion, abort and fault handling on the
   attached RP2040.
3. Add hardware edge/level gates, start triggers, finite retriggering and timeout
   supervision. Gate transitions execute in PIO; timeout supervision is explicitly
   a Python/timer service with documented resolution. Verify edges and timeouts
   using a second state machine as the signal source.
4. Add coordinated waveform groups, duty cycle, phase and complementary dead time.
   Verify simultaneous channels and independently inspect pulse periods and gaps.
5. Add desktop timeline preview, validation, quantisation reports, named parameters
   and deterministic scans. Use the same compiled edges as hardware execution.
6. Add PIO/DMA capture, edge timestamps, intended/observed trace comparison and
   experiment records. Verify known-good traces and deliberately wrong traces.
7. Add continuous-phase triangular frequency chirps. The one-way sweep time is
   independent of ON duration; longer pulses repeat up/down sweeps. Verify a
   90–120 kHz sweep with 2 ms per leg throughout one 20 ms ON section, including
   reversals, pulse count and phase continuity without CPU divider writes.

## Design constraints

- Core compilation and records import on CPython and MicroPython. Hardware imports
  are confined to execution and capture. No simulation is presented as hardware
  verification.
- Preparation explicitly consumes supplied generators/factories and their ordinary
  Python side effects. Infinite generators require a finite window or explicit bound;
  overflowing limits fails rather than silently truncating the experiment.
- The prepared backend consumes packed, constant-width PIO command/delay packets.
  Its minimum edge interval and memory limit are validated before arming outputs.
  A terminal PIO instruction acknowledges actual completion independently of DMA
  completion or FIFO occupancy.
- GPIO/state-machine ownership is checked. Groups in one PIO block start with one
  register write. RP2350 neighbour controls extend this across PIO blocks; RP2040
  groups spanning blocks require a common hardware trigger for aligned starts.
- The current device shares its clock with the internal probe. Digital relative
  timing can be verified; analogue quality and independent absolute calibration
  remain outside this validation setup.

## Completed scope and validation

All six requested capabilities are implemented using generators. Validation on
2026-11-09 includes 49 host tests, four upstream register-layout tests, a strict
documentation build, wheel installation/CLI checks, and both attached boards:
Tiny 2040 with MicroPython 1.29.0 and Pico Plus 2 with Pimoroni 1.29.0-2.

| Capability | Implementation and acceptance evidence |
|---|---|
| Buffered lifecycle | Finite/windowed generator compilation; prepare/start/wait/abort/rearm; final masks; injected underrun and timeout checks on both chips |
| Hardware gates | PIO edge/level waits, trigger/retrigger, software timeout supervision; held-HIGH rejection and 30 repeated ready gates, including forced PSRAM buffers |
| Coordinated waveforms | Atomic groups, independent frequencies, duty/phase and complementary dead time; RP2350 starts across PIO blocks verified by capture |
| Analysis and scans | Same generator interpreter; SVG preview, validation/quantisation/pulse counts; ordinary keyword parameters and a validated nine-trial scan |
| Capture and records | Finite PIO/DMA sampling, overrun detection, timestamps, coverage-aware comparison, CSV/VCD; hashed device records downloaded and revalidated on desktop |
| Adiabatic sweeps | Independent one-way sweep time and ON duration; continuous triangular reversal; full 20 ms/2,100-cycle output captured on both chips with zero transition mismatches |

The exact scripts and measured limits are recorded in `tests/hardware/README.md`.
The upstream `rp2040hw` fix corrects native PIO register stride/scalars and RP2350
DMA request selectors, verified against all 8/12 state machines. Other peripheral
modules in that library have not been comprehensively ported to RP2350.

Prepared windows are finite buffers, without automatic streaming refill. Gates
hold their last digital levels. Gate timeouts, print notifications and peer-fault
cleanup have interpreter/service latency. Capture is relative to the chip's own
clock. Analogue driver behaviour and atomiser response remain lab measurements.
