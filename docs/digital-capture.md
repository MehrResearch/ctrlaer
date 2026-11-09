# Digital capture

`DigitalCapture` records a finite, packed digital trace using another PIO state
machine and RX DMA. It can observe driven output pads on the same chip, so local
readback requires no jumper wire. A sibling microcontroller can capture connected
3.3 V digital signals with a shared ground. Neither setup measures analogue signal
quality; same-chip readback also shares the output clock.

```python
from ctrlaer import CtrlAer, pulse, compile_program, Trigger, DigitalCapture

controller = CtrlAer(0, 0, 1)
def experiment():
    yield pulse(100000), 20

program = compile_program(experiment)
run = controller.prepare(program)
capture = DigitalCapture(4, 0, sample_hz=8000000, duration_ms=21,
                         trigger=Trigger(0))
try:
    capture.start()  # Arm the probe before producing the trigger edge.
    run.start().wait(timeout_ms=1000)
    trace = capture.wait(timeout_ms=1000)
    print(trace.compare(program, tolerance_ns=250))
    with open("trace.vcd", "w") as stream:
        trace.write_vcd(stream)
finally:
    capture.close()
    controller.close()
```

Input pads must be enabled as inputs or as existing outputs. Capture does not
change their GPIO function or output direction. It claims only its state machine.
The finite sampler uses two PIO cycles per sample, limiting the rate to half the
system clock. Metadata reports the actual
rate after divider quantisation. The default buffer limit is 64 KiB. Three-channel
and other non-power-of-two widths are packed correctly, including a partial final
word. A hardware trigger can choose an edge or level; `wait` has a software timeout
that stops an untriggered/incomplete capture.
The sampler stops itself after the packed buffer's final sample. A DMA-induced
stall raises `ctrlaer_capture.CaptureOverrunError` and marks the trace invalid;
it is never treated as a uniformly sampled successful capture.

`trace.edges(channel)` yields sample indices and levels; `timestamps` yields
nanoseconds relative to sample 0. These are hardware-sampled relative timestamps.
GPIO synchronisers and trigger execution introduce a fixed capture-origin offset.
`write_csv` exports channel-major transition rows; `write_vcd` exports chronological
digital transitions for a standard waveform viewer. The raw sampled words remain
available for later analysis.

`trace.compare` checks expected transition levels and times against the exact
compiled buffer. Its default alignment estimates one common start offset across
all channels; it preserves relative channel phase. Set `align_first_edge=False`
when the time origin is externally calibrated. The default tolerance is one sample
period. A constant trace must also match the expected constant level.

For gates, supply measured external waiting times in `gate_wait_ticks`, one per
gate, in the program's output-clock ticks. Unknown waits cannot be inferred from
the prepared buffer alone and are rejected. A `channels` dictionary can map
program channels to a subset of captured inputs. Missing pulses, extra pulses,
wrong levels and excessive timing deviations are reported as mismatches. A short
capture that does not cover the intended program is inconclusive rather than a
whole-program pass. Set `require_complete=False` explicitly to compare only the
observed window, and inspect the returned coverage information.
