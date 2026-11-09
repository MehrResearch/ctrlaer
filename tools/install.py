#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mpremote==1.29.0", "mpy-cross==1.29.0.post2"]
# ///
"""Install or upgrade CtrlAer, compiling native .mpy modules for the chosen RP2.

Run with ``uv run tools/install.py`` to list USB devices and choose one. Repeat
that command after updating the checkout to upgrade. A standalone copy downloads
the selected GitHub ref; --source explicitly selects a checkout. Existing app
files are retained. The selected interpreter is restarted by mpremote; stop any
experiment before installing. Firmware itself is not flashed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import quote
from urllib.request import urlopen

REPOSITORY = 'MehrResearch/ctrlaer'
PREFIX = 'CTRLAER_JSON:'


def devices():
    """Enumerate ports without opening/resetting any connected interpreter."""
    from serial.tools import list_ports
    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    usb = [port for port in ports if port.vid is not None]
    return usb or ports


def show_devices(ports):
    if not ports:
        print('No USB serial devices found.')
    for number, port in enumerate(ports, 1):
        serial = port.serial_number or 'serial unavailable'
        print(f'{number}. {port.device} — {port.description} — {serial}')


def select_device(requested, ports, input_fn=input):
    """Resolve a port/USB serial, or explicitly ask the user to select a port."""
    if requested:
        matches = [port.device for port in ports
                   if requested in (port.device, port.serial_number)]
        if len(matches) > 1:
            raise ValueError('That serial number identifies more than one port; specify the full port.')
        return matches[0] if matches else requested
    show_devices(ports)
    if not ports:
        raise ValueError('Connect a MicroPython RP2 board and try again.')
    if not sys.stdin.isatty() and input_fn is input:
        raise ValueError('Selection requires a terminal; pass --device PORT or USB_SERIAL.')
    while True:
        try:
            answer = input_fn(f'Select device [1–{len(ports)}], or q to quit: ').strip()
        except EOFError:
            raise ValueError('No selection received; pass --device PORT or USB_SERIAL.') from None
        if answer.lower() in ('q', 'quit'):
            raise SystemExit(0)
        if answer.isdigit() and 1 <= int(answer) <= len(ports):
            return ports[int(answer) - 1].device
        print('Enter one of the listed numbers, or q.')


def remote(device, code):
    """Execute one bounded operation using mpremote's public CLI."""
    command = [sys.executable, '-m', 'mpremote', 'connect', device, 'exec', code]
    result = subprocess.run(command, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(PREFIX):
            return json.loads(line[len(PREFIX):])
    return None


def board_info(device):
    return remote(device, """
import sys,json
from machine import freq
print('CTRLAER_JSON:'+json.dumps({'platform':sys.platform,
 'board':sys.implementation._machine,'version':list(sys.implementation.version),
 'firmware':sys.version,'clock_hz':freq(),'mpy':sys.implementation._mpy}))
""")


def source_tree(source, ref, temporary):
    """Snapshot declared checkout modules, or download them from GitHub."""
    if source is None and ref is None:
        candidate = Path(__file__).resolve().parent.parent
        if (candidate / 'ctrlaer.py').is_file() and (candidate / 'pyproject.toml').is_file():
            source = candidate
    if source is not None:
        checkout = Path(source).resolve()
        metadata = tomllib.loads((checkout / 'pyproject.toml').read_text())
        provenance = {'kind': 'checkout'}
        try:
            provenance['commit'] = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True, stderr=subprocess.DEVNULL).strip()
            provenance['dirty'] = bool(subprocess.check_output(
                ['git', 'status', '--porcelain'], cwd=checkout, text=True))
        except (OSError, subprocess.CalledProcessError):
            pass
    else:
        ref = ref or 'main'
        base = f'https://raw.githubusercontent.com/{REPOSITORY}/{quote(ref, safe="")}/'
        with urlopen(base + 'pyproject.toml', timeout=30) as response:
            metadata = tomllib.loads(response.read().decode())
        provenance = {'kind': 'github', 'repository': REPOSITORY, 'ref': ref}
    root = temporary / 'source'
    root.mkdir()
    modules = metadata['tool']['setuptools']['py-modules']
    for name in modules:
        if not re.fullmatch(r'ctrlaer(?:_[a-z0-9_]+)?', name):
            raise ValueError(f'Unexpected module in package metadata: {name!r}')
        if source is None:
            with urlopen(base + name + '.py', timeout=30) as response:
                (root / (name + '.py')).write_bytes(response.read())
        else:
            # Compile and fingerprint the same bytes even if the working
            # checkout is edited while the board upload is in progress.
            (root / (name + '.py')).write_bytes((checkout / (name + '.py')).read_bytes())
        if not (root / (name + '.py')).is_file():
            raise ValueError(f'Missing source module: {name}.py')
    return root, modules, metadata['project']['version'], provenance


def compile_modules(root, modules, architecture, directory):
    directory.mkdir()
    files = []
    for name in modules:
        path = directory / (name + '.mpy')
        subprocess.run([sys.executable, '-m', 'mpy_cross', '-march=' + architecture,
                        '-s', name + '.py', '-o', str(path), str(root / (name + '.py'))], check=True)
        files.append(path)
    return files


ACTIVATE = """
import os,sys,json,hashlib,binascii
manifest=json.loads(%r)
stage='/lib/.ctrlaer-stage'
backup='/lib/.ctrlaer-previous'
def exists(path):
 try:os.stat(path);return True
 except OSError:return False
def fingerprint(path):
 digest=hashlib.sha256()
 with open(path,'rb') as stream:
  while True:
   block=stream.read(512)
   if not block:break
   digest.update(block)
 return binascii.hexlify(digest.digest()).decode()
for name,expected in manifest['files'].items():
 if fingerprint(stage+'/'+name)!=expected:raise ValueError('Staged hash mismatch: '+name)
try:os.mkdir(backup)
except OSError:pass
moved=[]
installed=[]
try:
 for name in manifest['modules']:
  for suffix in ('.py','.mpy'):
   old='/lib/'+name+suffix
   saved=backup+'/'+name+suffix
   if exists(old):
    if exists(saved):os.remove(saved)
    os.rename(old,saved)
    moved.append((saved,old))
 for name in manifest['files']:
  destination='/lib/'+name
  os.rename(stage+'/'+name,destination)
  installed.append(destination)
 for name in manifest['modules']:
  loaded=__import__(name)
  if getattr(loaded,'__file__','')!='/lib/'+name+'.mpy':
   raise ValueError('Import shadowed: '+name+' at '+getattr(loaded,'__file__','unknown'))
 os.sync()
 with open('/lib/ctrlaer-installed.json.tmp','w') as stream:json.dump(manifest,stream)
 os.rename('/lib/ctrlaer-installed.json.tmp','/lib/ctrlaer-installed.json')
except BaseException:
 for path in installed:
  if exists(path):os.remove(path)
 for saved,original in reversed(moved):os.rename(saved,original)
 for name in manifest['modules']:
  if name in sys.modules:del sys.modules[name]
 os.sync()
 raise
print('CTRLAER_JSON:'+json.dumps({'installed':manifest['version'],'modules':len(installed),'verified':True}))
"""


def install(device, source=None, ref=None):
    info = board_info(device)
    if info['platform'] != 'rp2':
        raise ValueError('This installer supports RP2040/RP2350 MicroPython boards.')
    if tuple(info['version'][:3]) < (1,29,0):
        raise ValueError('MicroPython 1.29.0 or newer is required by this precompiled installer. '
                         'Update board firmware first; existing application files have not been changed.')
    architecture = 'armv7emsp' if 'RP2350' in info['board'] else 'armv6m'
    print(f"Selected {info['board']} on {device}\n{info['firmware']}", flush=True)
    with tempfile.TemporaryDirectory(prefix='ctrlaer-install-') as temporary:
        temporary = Path(temporary)
        root, modules, version, provenance = source_tree(source, ref, temporary)
        print(f'Compiling {len(modules)} modules for {architecture}…', flush=True)
        files = compile_modules(root, modules, architecture, temporary / 'compiled')
        manifest = {'schema': 'ctrlaer.install/v1', 'version': version,
                    'architecture': architecture, 'source': provenance,
                    'firmware': info, 'modules': modules,
                    'source_sha256': {name: hashlib.sha256((root / (name + '.py')).read_bytes()).hexdigest()
                                      for name in modules},
                    'files': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
        remote(device, "import os\nfor path in ('/lib','/lib/.ctrlaer-stage'):\n try:os.mkdir(path)\n except OSError:pass")
        print('Uploading to staging directory…', flush=True)
        uploaded = subprocess.run([sys.executable, '-m', 'mpremote', 'connect', device,
                                   'fs', 'cp', *(str(path) for path in files), ':/lib/.ctrlaer-stage/'],
                                  text=True, capture_output=True, timeout=180)
        if uploaded.returncode:
            raise RuntimeError((uploaded.stderr or uploaded.stdout).strip())
        print('Verifying and activating; previous library files are retained…', flush=True)
        result = remote(device, ACTIVATE % json.dumps(manifest))
        if not result or not result.get('verified'):
            raise RuntimeError('Device did not confirm a verified installation.')
    print(f"CtrlAer {result['installed']} installed and verified ({result['modules']} modules).", flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', help='Serial port or USB serial number; omitted: enumerate and select')
    parser.add_argument('--list', action='store_true', help='List devices without resetting them')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--source', type=Path, help='Install/upgrade from this checkout')
    group.add_argument('--ref', help='Install/upgrade from a GitHub branch, tag or commit (standalone default: main)')
    args = parser.parse_args(argv)
    ports = devices()
    if args.list:
        show_devices(ports)
        return 0
    try:
        device = select_device(args.device, ports)
        install(device, args.source, args.ref)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Installation failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
