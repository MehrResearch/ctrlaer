# Experiment capabilities implementation

This work preserves the existing generator DSL and adds an inspectable prepared
program path. No changes are made to the untracked `examples/testing/` directory.

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
7. Add continuous-phase linear frequency chirps, including a 90–120 kHz sweep in
   one 20 ms ON section. Verify endpoints, pulse count, duration and representative
   measured periods without CPU-timed clock-divider changes.

## Design constraints

- Core compilation and records import on CPython and MicroPython. Hardware imports
  are confined to execution and capture. No simulation is presented as hardware
  verification.
- Preparation does not execute arbitrary generators unless the caller explicitly
  supplies one. Infinite generators require a finite window or an explicit bound;
  overflowing limits fails rather than silently truncating the experiment.
- The prepared backend consumes packed, constant-width PIO command/delay packets.
  Its minimum edge interval and memory limit are validated before arming outputs.
  A terminal PIO instruction acknowledges actual completion independently of DMA
  completion or FIFO occupancy.
- GPIO/state-machine ownership is checked. Groups in one PIO block start with one
  register write; groups spanning blocks require a common hardware trigger for
  precisely aligned starts.
- The current device shares its clock with the internal probe. Digital relative
  timing can be verified; analogue quality and independent absolute calibration
  remain outside this validation setup.

This file records the full intended scope, not a claim of completion. Each stage
must have implementation, documentation and relevant host/hardware evidence.
