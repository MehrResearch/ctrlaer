# Hardware checks

These MicroPython scripts were run on otherwise idle boards on 2026-11-09 and 2026-11-10:

| Board | Firmware | System clock |
|---|---|---|
| Pimoroni Tiny 2040 2MB | Official MicroPython 1.29.0 | 125 MHz |
| Pimoroni Pico Plus 2 | Pimoroni `pico_plus2` 1.29.0-2, with PSRAM | 150 MHz |

GP8/GP9 are unused internal pads on the Tiny 2040 and unconnected GPIO on the
tested Pico Plus 2. Check wiring before reusing this pin choice. Each script
stops its own state machines and returns tested pads to inputs in cleanup.

| Check | Tiny 2040 | Pico Plus 2 | Evidence |
|---|---|---|---|
| `legacy_completion.py` | Pass | Pass | 200 ms command waits about 202 ms, rather than returning at FIFO empty |
| `prepared_lifecycle.py` | Pass | Pass | Completion, rearm, abort to HIGH, timeout to LOW, injected TX underrun |
| `triggers.py` | Pass | Pass | Rising/falling gates, level-ready start, held-HIGH edge timeout, 20 retriggered pulses |
| `waveform_capture.py` | Pass | Pass | Duty/phase, merged frequencies, complementary non-overlap, independent grouped carriers |
| `chirp.py` | Pass | Pass | Full 20 ms pulse, 2 ms per sweep leg, 2,100 cycles; all 4,199 sampled transitions agree within 250 ns |
| `effects.py` | Pass | Pass | Managed print events replayed twice; sleep becomes a timed hold |
| `records.py` | Pass | Pass | Actual device run/capture saved, downloaded and validated on desktop, including vendor firmware string |
| `robustness.py` | Pass | Pass | Ownership conflicts; background member fault aborts peers; injected capture overrun; capture timeout/reuse and clock checks |
| `ready_gate_stress.py` | Pass | Pass | 30 timeout-then-ready transitions; all 30 ready-run DMA buffers explicitly placed in RP2350 PSRAM |
| `rp2350_groups.py` | N/A | Pass | Atomic coordinated start across PIO0/PIO2, captured by PIO1 |

The triangular sweep's nine reversal points fall at 16,000-sample intervals in
the 8 MS/s capture. With precompiled modules and native compilation, cold first-output latency for
the 2 ms-leg chirp is 366 ms on Tiny and 291 ms on Pico Plus 2 in the recorded cold run. See
[the performance guide](../../docs/performance.md) for the measurement method;
the compiler restores the caller's GC threshold. The packed
20 ms waveform occupies 33,616 bytes. The Tiny has ample heap after scoped GC;
larger programs still need explicit bounds or desktop compilation.

Fixed 512-tick control packets prevent short bursts of gate/event traffic from
starving DMA buffers in PSRAM. Measured gate-release response is about 12.3 us on
RP2040 and 10.3 us on RP2350. This is deterministic PIO control overhead, separate
from the 1 ms software notification/timeout service. Sustained dense playback
still depends on available memory bandwidth; stalls are reported, not hidden.

The finite capture sampler uses two PIO cycles per sample and stops in hardware.
An injected RX stall raises `CaptureOverrunError`; invalid timestamps cannot pass
trace comparison. Trigger-origin uncertainty is handled within the explicitly
selected sample tolerance, with one alignment shared across channels.

Run a check from the repository root:

```sh
uv run tools/install.py --device /dev/cu.usbmodem3101
python3 tools/board.py --port /dev/cu.usbmodem3101 \
    tests/hardware/prepared_lifecycle.py --result result.json
```

The installer provides precompiled modules; avoid `--deploy`, which uploads
source copies that can shadow them and increase startup cost. The POSIX serial
tool resets the interpreter but does not flash firmware. Its default observation
timeout is 60 s; use `--timeout 120` for a longer analysis.

The upstream `rp2040hw/tests/hardware_pio.py` additionally passed all eight RP2040
and twelve RP2350 state-machine register checks, IRQ addressing and chip-specific
DMA request constants. See that repository's `tests/HARDWARE.md`.

The probe shares the output oscillator. These tests establish relative digital
timing, not independent absolute calibration, analogue drive quality or atomiser
resonance/actuation. The checks above used no external oscilloscope or inter-board
wiring.

## Independent inter-board capture

Connect the same GPIO on the Tiny 2040 and Pico Plus 2, and connect their grounds.
With the current modules installed on both otherwise idle boards, run:

```sh
python3 tools/cross_board.py --tiny-port /dev/cu.usbmodem3101 \
    --pico-port /dev/cu.usbmodem101 --gpio 6 --no-leds --out-dir /tmp/ctrlaer-link
```

The host arms the receiver before starting the sender, then exchanges their roles.
`--gpio` selects a shared header GPIO from 0 through 7 (default: 6).
Only one end drives the link at a time; both ends are returned to inputs afterward.
The Tiny RGB LED is blue for sending and green for receiving. The Pico Plus 2 has
a single-colour LED: steady for sending and blinking for receiving. An unexpected
test-tool exception sets error indicators. The last roles remain visible after a
completed test, even when a signal comparison fails. Use `--no-leds` for dark
unattended testing; it suppresses all role and error indicators.

The 25.1 ms pattern includes a 100 us start marker, 10 kHz/25% and
108.05 kHz/40% bursts, and the full 20 ms triangular chirp. The receiver captures
26 ms at 10 MS/s using its own oscillator. Both exact prepared and captured buffers
are downloaded in small chunks, hash-checked and stored in experiment records;
VCD files and a combined `results.json` accompany them. A failed waveform match
is retained and the other direction is still tested. Exit status is nonzero if
either direction fails. `--direction` selects a single direction when diagnosing
wiring.

Acceptance requires matching edge counts/levels, a complete ordinary comparison
within 2 us, relative clock difference within 100 ppm, and at most 200 ns residual
after fitting that clock difference. The fit is reported separately from the
ordinary comparison and does not turn missing or extra pulses into a pass.
Independent oscillators permit a relative frequency check; neither is a calibrated
absolute reference.

Observed on the original GPIO7/GND setup on 2026-11-09 and 2026-11-10: Pico-to-Tiny passed with
2,337 rising edges, zero mismatches,
about 9 ppm relative clock difference and a maximum fitted residual of 53 ns.
Tiny-to-Pico instead produced a constant-HIGH capture with no RX overrun. Slow
GPIO tests reproduced the stuck-HIGH behaviour; briefly disabling the Pico input
buffer cleared it. This characterises that original link/input-stage setup;
the failed capture is retained as diagnostic evidence.

The follow-up on GPIO6 with a different Pico ground connection passed in both
directions, using unchanged waveform/runtime modules. Slow HIGH/LOW checks also
passed (100 receiver samples per direction). Each full capture contains 2,337
rising edges and zero mismatches; fitted clock differences are about -9/+9 ppm
and maximum residuals are 51/53 ns at 100 ns sample resolution. Both GPIO and
ground connection changed, so this does not isolate the cause of the earlier
GPIO7 failure.

## Incremental execution checks

Use `--timeout 300` for `buffered_stress.py`; its seven workloads take about four
minutes. The default `SOAK_SECONDS=30` is defined in the script. All other checks
below fit the helper's default observation timeout.


| Check | Tiny 2040 | Pico Plus 2 | Evidence |
|---|---|---|---|
| `dma_ring.py` | Pass | Pass | Circular TX channels reload their finite counts and repeat safely |
| `buffered.py` | Pass | Pass | Cold start below 500 ms, continuous refill, explicit collection, rearm, repeated gates, timeout/final mask, intentional underrun, 100 completion/abort races, disjoint shared-pool geometry |
| `buffered_stress.py` | Pass | Pass | Seven 30-second workloads: pulse, periodic/non-periodic chirps, repeated shots and default sweep; normal GC enabled |
| `buffered_matrix.py` | Pass | Pass | Six five-second workloads: original integer, two/five-channel mux, repeated chirps, descending chirp and default 20 ms sweep |
| `eager.py` | Pass | Pass | Output held across Python gaps, ordered pin observations, two gates/history, error final mask, abort/close between fragments |
| `tools/start_latency.py` | Pass | Pass | Peer-captured cold first-output latency below 500 ms in finite/buffered/eager modes |

The eager capture spans 700 ms at 100 kS/s: Python and fragment-preparation gaps
are deliberately outside its real-time guarantees. Capture is on the output pad
itself, with a separate state machine and no external jumper.

The default 20 ms-leg sweep additionally passed independent bidirectional capture
with a 100 ms ON section. Each direction contained 21,474 transitions with zero
mismatches; maximum fitted residuals were 127/128 ns at 4 MS/s. Cold marker-to-first
output latency was below 500 ms for both 2 ms and default sweep rates in all three
explicit modes, including eager startup on the Tiny after removing empty-fragment
preparation. The final default-sweep observations were 387/442/437 ms on Tiny
and 304/367/364 ms on Pico for finite/buffered/eager execution respectively.
See the performance guide for the measurement scope and table.

A non-periodic 90.001–120 kHz chirp passed exact host/device packet identity and
bidirectional capture: 2338 expected rises per 20 ms test, zero mismatches,
50/52 ns fitted residuals at 10 MS/s. Its 100 ms buffered test crossed many chunk
boundaries: 21,476 transitions per direction, zero mismatches and 127/128 ns
residuals at 4 MS/s. The compact default-sweep cache's corresponding 100 ms test
also passed in both directions (21,474 transitions, 127/128 ns residuals).

The portable suite checks generic inverse-phase lookup against the independent
analytic model within one system clock, including phase/duty/dead time, sweep
reversals and local-coordinate rollover. Symmetric cache folding is enabled
only at matching half-cycle boundaries; byte-delta compression preserves the
stored intervals exactly. Lifecycle tests also exercise capture beside eager
playback, reused pools with reset marker rings, and completion/abort races.

[Machine-readable validation report](validation-2026-11-10.json) records the
final twelve cold starts, six independent captures, fourteen sustained workloads,
lifecycle snapshots and hashes of all ten modules installed on each board.
