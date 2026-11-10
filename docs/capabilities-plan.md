# Design and validation

CtrlAer uses generators as its experiment-authoring interface. Analysis and
playback interpret the same commands. Ordinary Python remains ordinary Python:
global mutations, random state, file I/O and third-party calls run when a
factory or generator advances. CtrlAer does not attempt to undo those effects.

## Execution layers

| Layer | Responsibility |
|---|---|
| Generator vocabulary | Integer states, `pulse`, `chirp`, `mux`, managed sleep/print/wait |
| Finite compiler | Complete clock-based schedule, validation, preview and exact replay artifacts |
| Incremental compiler | Resumable commands, bounded chunks and event metadata |
| PIO engine | Clock-timed output, holds, GPIO waits, completion and unpublished-successor guards |
| DMA runtime | Queue prepared data; publish successors; supervise events, timeouts and errors |
| Capture/analysis | Packed input samples, edge timestamps, coverage-aware comparison and records |

Finite prepared output uses a complete validated buffer. Buffered output keeps
four transmit DMA channels and a prepared spare ahead of the compiler. An
allocation-free hard interrupt publishes a ready block; generator work remains
in the soft service context. A missed successor leaves a blocking PIO guard
intact, preventing stale data from being played. The software service then
reports the underrun and applies the configured final mask.

Eager execution uses that same engine for one yielded command at a time. Python
runs after completion, with the last output level held during the next decision
and preparation. Its inter-command gaps are deliberately outside real-time
scheduling guarantees. See [execution modes](execution-modes.md).

Native fixed-point kernels generate and pack carriers without per-edge Python
allocation. A bounded chirp interval cache is used only when both frequency and
integrated phase truly repeat. Local-coordinate rollover preserves indefinite
phase progress. Non-repeating chirps use a bounded inverse-phase table with
one-system-clock edge accuracy, while retaining fractional phase across sweeps.
Waveform pools share alignment space with scratch storage to
reduce pressure on the Tiny's non-moving heap.

## Timing and resource invariants

- GPIO/state-machine ownership is checked before arming. Controllers must use
  non-overlapping output GPIOs and distinct state machines.
- Output count and system clock must match the compiled schedule. A changed
  system clock is an execution error; changing controller frequency cannot
  retime an already compiled buffer.
- Completion is acknowledged by PIO, independently of FIFO occupancy and DMA
  completion. Gate timeouts and observation timestamps remain soft-service work.
- Buffer, event and history storage are bounded for incremental playback.
  Arbitrary user Python can still allocate or block without a bound.
- Group starts are atomic within one PIO block. RP2350 neighbour controls extend
  this across blocks; RP2040 cross-block groups require a common hardware trigger.
- Capture records achieved sample rate and detects DMA stalls. A stalled or
  incomplete trace cannot establish a successful whole-program comparison.

## Evidence and reproduction

The portable test suite checks the compiler against an independent analytic
carrier model, chunk continuity, phase/sweep rollover, multiplexing, event order,
records and installer activation/rollback. The API reference is generated from
source signatures/docstrings and checked in CI. Documentation builds in strict
mode; its waveform figures are regenerated from actual compiled programs.

Hardware validation uses a Tiny 2040 with MicroPython 1.29.0 and a Pico Plus 2
with Pimoroni 1.29.0-2. It includes lifecycle/fault tests, triggered indefinite
operation, multi-channel production, eager cancellation, physical cold-start
latency and bidirectional capture with independent oscillators. See
[performance and limits](performance.md) and the repository's
`tests/hardware/README.md` for commands and measured results.

The earlier upstream `rp2040hw` changes correct PIO register strides/scalars and
RP2350 DMA request selectors, checked across all 8/12 state machines. That does
not establish a comprehensive RP2350 port of every peripheral in that library.

A same-board probe shares the waveform clock. Two boards permit an independent
relative clock check, but neither is an absolute calibrated reference. Analogue
drive quality, mechanical response and atomisation remain laboratory measurements.
