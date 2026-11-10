# Original FIFO recipes

These scripts in `examples/` demonstrate the original integer-command API.
Their generators also explain patterns used in existing laboratory programs.
The [worked gallery](examples.md) covers explicit finite, buffered and eager
execution. The recipes here use `run(program)` with its original semantics.

For this API, `run` feeds the hardware FIFO. Its return does not establish that
the last interval has finished. After a finite run, use `controller.block()` to
wait for actual completion, then `controller.close()` to release the engine.
Closing the original engine retains the last digital level. Finish with an OFF
command when LOW is the required final output.

All figures here are nominal command references. The original oscillator's
fractional divider quantisation and delays while Python/USB feeds the FIFO can
affect a physical capture. The envelope figures represent a carrier-enabled
section by an ON band; their vertical scale does not show individual cycles.

## Two alternating levels — `stepper.py`

`phase1(10)` alternates 50 ms HIGH and 50 ms LOW ten times. `phase2(10)` starts
LOW and does the reverse. `mux()` places them on GP0 and GP1 for a one-second
sequence. These are constant levels; there is no high-frequency carrier in
this example.

![The two 50 ms phases over ten repetitions](assets/legacy-phases.svg)

The file name reflects its original application. The plotted phase signals must
be matched to the intended driver's input protocol and power stage. Connect
GP0/GP1 only to compatible logic inputs, with common GND. On the Tiny these are
header positions 16/15; on the Pico Plus 2 they are 1/2. A STEP/DIR driver's STEP
and DIR meanings must be considered when adapting the pattern to that driver.

## Milliseconds and carrier counts — `timing.py`

The controller carrier is 100 kHz. `step_ms()` emits five 200 ms ON sections,
each followed by 200 ms OFF. `step_ticks()` emits the same pattern with duration
20,000 and `use_ms=False`: 20,000 periods at 100 kHz is 200 ms. Running the script
submits the two sequences consecutively.

![Five 200 ms ON/OFF pairs; carrier envelopes](assets/legacy-timing.svg)

![First five cycles at 100 kHz](assets/legacy-timing-zoom.svg)

The `ticks` in this script are **carrier periods**, not system-clock ticks.
At 100 kHz a period is 10 µs. The script uses GP7: physical position 9 on the Tiny,
10 on the Pico Plus 2. Connect it to a driver/probe logic input and common GND.
The envelope plot shows one of the two equivalent two-second sequences.

## Slow modulation plus complementary carriers — `ultrasonic.py`

This indefinite program multiplexes five channels at a shared 40 kHz carrier:

| GPIO | Generator | Pattern |
|---|---|---|
| GP0 | `low_freq()` | 5 ms HIGH, 5 ms LOW: a 100 Hz level waveform |
| GP1, GP3 | `phase1()` | 5 ms `PULSE01`, then 1000 ms OFF |
| GP2, GP4 | `phase2()` | 5 ms `PULSE10`, then 1000 ms OFF |

![First 1010 ms, including the start of the second carrier burst](assets/legacy-ultrasonic.svg)

![First 100 µs; opposite half-cycle states are visible](assets/legacy-ultrasonic-zoom.svg)

Each 5 ms carrier burst contains 200 cycles and repeats every 1005 ms. GP1/GP3
start HIGH; GP2/GP4 start LOW. These opposite phases have no programmed dead time.
Use the [explicit complementary example](examples.md#set-duty-cycle-and-complementary-dead-time)
when a driver needs a defined both-LOW handover gap.

Connect GP0 through GP4 to five compatible logic inputs and common GND. Their
physical positions are 16, 15, 14, 13, 12 on the Tiny, or 1, 2, 4, 5, 6 on the
Pico Plus 2. Drivers, amplification and transducers depend on the experiment;
the GPIOs provide the plotted logic signals.

## Changing proportions with Python pauses — `blocking.py`

The source uses 50 composition steps and one repetition per step. Within a
100 ms dosing window, the amine ON duration increases from 0 to 100 ms and the
aldehyde duration decreases from 100 to 0 ms. Each window is followed by 250 ms
OFF. Both carriers use 111.5 kHz.

![Increasing/decreasing carrier envelopes across all 50 steps](assets/legacy-proportions.svg)

The horizontal axis shows programmed waveform time only. At each completed
step, the amine generator calls `ctrlaer.block()`, sleeps one second, drives the
air-control GPIO HIGH for five seconds, then LOW. Those ordinary Python pauses
and pin writes execute live as the generator advances. They pause production of
the shared `mux()` output too; the second generator is not an independently
running Python task. Console prints also run live.

The script first waits two seconds before submission. This initial delay and
the 50 reaction/air-control pauses are excluded from the envelope plot. They
cannot be previewed as hardware-timed CtrlAer sleep commands because the file
uses ordinary `time.sleep` and `machine.Pin` calls.

Use the Pico Plus 2 for the script's existing wiring:

| Connection | GPIO | Pico Plus 2 physical position |
|---|---|---:|
| Amine driver logic input | GP7 | 10 |
| Aldehyde driver logic input | GP8 | 11 |
| Air/valve driver logic input | GP16 | 21 |
| Logic ground | GND | 8, or another GND |

GP8/GP16 are not exposed on the Tiny's headers. For that board, deliberately
choose exposed pins and update both the two-channel base and air-control pin;
GP0/GP1 and GP6 are one possible mapping. Connect only driver logic inputs,
using a suitable power circuit for each load. The comments in the source now
match its actual GP7/GP8 base-pin assignment.

For new feedback programs, [eager execution](execution-modes.md#eager-measurements-and-ordinary-python-in-sequence)
provides explicit command completion before Python resumes. Use managed CtrlAer
`sleep`/`print`/`wait_for` when an effect belongs to the compiled waveform order.

## Commands over USB — `listen.py`

The board creates a one-channel 5 kHz controller on GP0 and calls `listen(stdin)`.
While that script owns stdin, send newline-terminated `state,duration_ms` lines.
It echoes accepted commands; `END` or EOF ends the listener. An echo acknowledges
submission, rather than electrical completion. Close other serial clients before
opening the host connection.

For example, these commands request the following nominal pattern:

```text
2,100
0,200
3,150
0,200
1,100
END
```

![5 kHz bursts and a constant HIGH section from the sample serial commands](assets/legacy-serial.svg)

`2` is LOW-first `ON`, `0` is OFF, `3` is constant HIGH and `1` is HIGH-first
`PULSE01`. Host pacing can add gaps, so serial arrival is not a hardware trigger.
Send a final OFF command if LOW is required before ending the session.
Connect GP0 to a compatible input and common GND: Tiny position 16 or Pico Plus 2
position 1. The host data connection is USB. See [API reference](api-reference.md)
for `listen` error handling and `block` completion semantics.
