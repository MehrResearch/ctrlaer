# Install and upgrade

The supported installation path compiles CtrlAer on your computer, uploads the
compiled modules, and verifies them on the selected board. The same command
installs a fresh copy or upgrades an existing installation.

## Requirements

- An RP2040 or RP2350 board running MicroPython 1.29.0 or newer with `rp2.DMA`.
- A USB data cable and a serial port your account can open.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) on your computer.
  uv obtains the Python interpreter and the script's dependencies automatically.
- A CtrlAer checkout, or a standalone copy of `tools/install.py`.

The installer uses [PEP 723 script metadata](https://docs.astral.sh/uv/guides/scripts/)
to pin `mpremote` and `mpy-cross`. You do not need to activate a virtual environment
or install those tools globally. Its Python requirement is 3.11 or newer; this is
the **desktop script's** requirement, not the MicroPython language version.

If the board does not yet run MicroPython, install its board-specific UF2 first.
Use the firmware download for the
[Raspberry Pi Pico](https://micropython.org/download/RPI_PICO/) or
[Pico 2](https://micropython.org/download/RPI_PICO2/), or find your exact board in
the [MicroPython downloads](https://micropython.org/download/). Hold BOOTSEL while
connecting USB, then copy the board's UF2 file to the drive that appears.
After it restarts, the board provides a MicroPython USB serial connection.
The CtrlAer installer updates the library; it does not flash firmware.

## Install from your checkout

Stop any running experiment and close Thonny, serial terminals or other programs
holding the board's serial port. The selected MicroPython interpreter will be
restarted during installation.

From the repository root:

```sh
uv run tools/install.py
```

With no `--device`, the script enumerates ports and asks you to select a number,
even if only one device is connected. For example:

```text
1. /dev/cu.usbmodem101 — Raspberry Pi Pico — USB_SERIAL_1
2. /dev/cu.usbmodem102 — Raspberry Pi Pico 2 — USB_SERIAL_2
Select device [1–2], or q to quit:
```

The descriptions and serial numbers above are examples. Choose the entry for
your board. Enter `q` to leave without opening a device.

To enumerate without restarting any interpreter:

```sh
uv run tools/install.py --list
```

To select explicitly, use either the serial port or its USB serial number:

```sh
uv run tools/install.py --device /dev/cu.usbmodem101
uv run tools/install.py --device USB_SERIAL_1
# Windows example:
uv run tools/install.py --device COM5
```

A USB serial number is useful when the operating system changes the port name.
Non-interactive use requires `--device`; the script never guesses which board to
update. It reports the board, firmware and compilation architecture, then ends
with `installed and verified` on success.

## Upgrade

Update the checkout to the revision you want, then repeat the installation:

```sh
git pull --ff-only
uv run tools/install.py
```

The installer reads the module list from that checkout's `pyproject.toml` and
rebuilds the modules for the selected chip. Use `--source /path/to/ctrlaer` to
select a different checkout. User application files such as `main.py`, `boot.py`
and unrelated libraries are retained.

For a standalone downloaded installer, select a GitHub release tag, branch or
commit explicitly:

```sh
uv run install.py --ref main
uv run install.py --ref COMMIT_SHA --device USB_SERIAL
```

Without `--source` or `--ref`, a script inside a checkout uses that checkout.
A standalone script downloads `main`. `--ref` also works inside a checkout and
selects the remote source. Prefer an immutable commit or release tag when
recording an experiment that must be reproducible. These commands require a
network connection when fetching dependencies or remote source.

## What is installed

CtrlAer modules go into `/lib` as `.mpy` files. The compiler targets ARMv6-M for
RP2040 and ARMv7E-M for RP2350. Native kernels are precompiled too, which avoids
their compilation cost at every soft reset. Do not also upload `.py` copies with
the same module names; they can take precedence over the compiled files.

The update proceeds in these stages:

1. Check board identity and firmware, then compile on the computer.
2. Upload to `/lib/.ctrlaer-stage` and verify every uploaded file's SHA-256.
3. Move prior matching `.py`/`.mpy` library files into `/lib/.ctrlaer-previous`.
4. Activate the new files and import every module to check its load path.
5. Write `/lib/ctrlaer-installed.json` with source hashes, revision information,
   firmware identity and compiled-file hashes.

An activation/import error restores the previous library files. The previous
directory is one generation of backup and is replaced on subsequent upgrades.
The multi-file update is not a power-loss transaction: if USB power disappears
during activation, rerun the installer after reconnecting.

## Check the installation

Open the board's REPL after installation:

```python
import ctrlaer
print(ctrlaer.__file__)  # /lib/ctrlaer.mpy

import json
with open('/lib/ctrlaer-installed.json') as stream:
    installed = json.load(stream)
print(installed['version'], installed['source'])
```

Continue with [your first waveform](getting-started.md).

## Resolve common failures

| Symptom | Action |
|---|---|
| No USB serial device appears | Check the data cable and firmware. A board in UF2 bootloader mode appears as a drive; install MicroPython before using this script. |
| Port busy / access denied | Close other REPL clients. On Linux, check your distribution's serial-port group permissions and reconnect after changing group membership. |
| Selection requires a terminal | Run interactively, or pass `--device PORT`/`USB_SERIAL`. |
| Firmware is too old | Upgrade the board-specific MicroPython firmware, then rerun. |
| `Import shadowed` | Move stale `ctrlaer*.py`/`.mpy` files out of the filesystem root or other earlier import paths, then rerun. The installer checks `/lib` paths before declaring success. |
| `incompatible .mpy` | Check that your firmware supports the pinned cross-compiler's `.mpy` format and native architecture. The installer rolls back an import failure; keep the error output when reporting a firmware-specific issue. |

For startup measurements and the distinction between import cost and waveform
production rate, see [performance](performance.md).

## Build and read these docs locally

From the checkout, run:

```sh
uv run mkdocs serve
```

Open the address printed in the terminal, usually `http://127.0.0.1:8000/ctrlaer/`.
The page reloads when you edit a guide. To check the complete site and write it
to `site/`, use:

```sh
uv run mkdocs build --strict
```
