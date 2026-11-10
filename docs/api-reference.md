# API reference

Use the reference to look up arguments, defaults and return values. The
[getting-started guide](getting-started.md) explains the program structure;
[worked examples](examples.md) show complete experiments and connections.

| Reference | Objects |
|---|---|
| [Controllers](api/controller.md) | `CtrlAer` |
| [Signals and timing](api/signals.md) | `square_wave`, `sweep`, `sleep`, `print`, `mux` |
| [Triggers and groups](api/triggers.md) | `Trigger`, `wait_for`, `Group` |
| [Run objects](api/runs.md) | `PreparedRun`, `BufferedRun` |
| [Previews and compiled programs](api/analysis.md) | `compile_program`, `CompiledProgram`, `render_svg`, `parameter_scan`, `program_to_dict`, `program_from_dict` |
| [Digital measurements](api/capture.md) | `DigitalCapture`, `Trace` |
| [Experiment records](api/records.md) | `save_record`, `load_record`, `fingerprint`, `device_info` |
| [Errors](api/errors.md) | `UnderrunError`, `ExecutionTimeoutError`, `GateTimeoutError`, `CaptureOverrunError` |

The controller, input waits and digital capture run on MicroPython. Previews,
parameter scans and record loading also work on your computer. The signatures
and descriptions on these pages come from the library's source docstrings.
