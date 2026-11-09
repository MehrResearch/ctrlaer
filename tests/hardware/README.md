# Hardware checks

These MicroPython scripts were run on otherwise idle boards on 2026-11-09:

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
the 8 MS/s capture. Compilation takes approximately 4.5 s on Tiny 2040 and 2.0 s
on Pico Plus 2; the compiler restores the caller's GC threshold. The packed
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
python3 tools/board.py --port /dev/cu.usbmodem3101 --deploy \
    tests/hardware/prepared_lifecycle.py --result result.json
```

`--deploy` resets the interpreter and overwrites `/lib/ctrlaer*.py`; unrelated files
are retained. The POSIX serial tool does not flash firmware. Its default observation
timeout is 60 s; use `--timeout 120` for a slower board or larger analysis.

The upstream `rp2040hw/tests/hardware_pio.py` additionally passed all eight RP2040
and twelve RP2350 state-machine register checks, IRQ addressing and chip-specific
DMA request constants. See that repository's `tests/HARDWARE.md`.

The probe shares the output oscillator. These tests establish relative digital
timing, not independent absolute calibration, analogue drive quality or atomiser
resonance/actuation. No external oscilloscope or inter-board wiring was used.
