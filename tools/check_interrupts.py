#!/usr/bin/env python3
"""Check USB Ctrl-C and soft-reset recovery on a loopback-connected test board.

Tiny 2040: GP2 -> GP6. Pico Plus 2: GP1 -> GP8. Uses GP18 as output.
This runs the same stop / raw-REPL soft-reset sequence used when rerunning a
program in Thonny. Optional --thonny-backend loads that installation's helper
code too. It does not change board firmware or library files.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import select
import textwrap
import time

from board import Board, read_until


SOURCE = '''
from ctrlaer import CtrlAer, HIGH, ON, OFF, wait_for
from machine import Pin
from time import sleep_ms
import sys
tiny = 'Tiny 2040' in sys.implementation._machine
source_pin, input_pin = (2, 6) if tiny else (1, 8)
source = Pin(source_pin, Pin.OUT, value=0)
Pin(input_pin, Pin.IN, Pin.PULL_DOWN)
def shots():
    while True:
        wait_for(pin=input_pin)
        yield command, duration
        yield OFF, duration
controller = CtrlAer(sm_number=0, base_pin=18, n_pins=1, freq=100000)
try:
    print('PREPARE')
    run = controller.prepare(shots, mode='buffered')
    print('START')
    run.start()
    print('GATE')
    if phase != 'GATE':
        sleep_ms(2)
        source.value(1)
        sleep_ms(2)
        source.value(0)
    if phase == 'LOW':
        sleep_ms(int(duration + 2))
    print('WAIT')
    run.wait()
finally:
    controller.close()
    if phase != 'PREPARE':
        assert Pin(18).value() == 0, 'final level was not applied'
    source.value(0)
    if tiny:
        for gpio in (18, 19, 20):
            Pin(gpio, Pin.OUT, value=1)
    else:
        Pin('LED', Pin.OUT, value=0)
    print('CLOSED')
'''

CHECK = '''
from machine import mem32
from rp2 import DMA
from ctrlaer_runtime import _owners, _pins
assert not _owners and not _pins, str((_owners, _pins))
for pio in range(3 if 'RP2350' in sys.implementation._machine else 2):
    assert not mem32[0x50200000 + pio * 0x100000] & 15
channels = []
try:
    for _ in range(16 if 'RP2350' in sys.implementation._machine else 12):
        channels.append(DMA())
finally:
    for channel in channels:
        channel.close()
print('REUSABLE')
'''


def thonny_helper(path):
    tree = ast.parse(path.read_text())
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == '_get_helper_code')
    env = {'dedent': textwrap.dedent, 'OBJECT_LINK_START': '<object>',
           'OBJECT_LINK_END': '</object>', 'MGMT_VALUE_START': b'<value>',
           'MGMT_VALUE_END': b'</value>', 'ENCODING': 'utf-8'}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), env)
    return env['_get_helper_code'](None)


def submit(board, source):
    os.write(board.fd, b'\r\x01')
    read_until(board.fd, b'raw REPL; CTRL-B to exit\r\n>')
    data = source.encode()
    for offset in range(0, len(data), 128):
        os.write(board.fd, data[offset:offset+128])
        time.sleep(.002)
    os.write(board.fd, b'\x04')
    read_until(board.fd, b'OK')


def read_marker(board, marker):
    output = bytearray()
    deadline = time.monotonic() + 5
    while marker not in output:
        if time.monotonic() > deadline:
            raise TimeoutError(repr(bytes(output)))
        if select.select([board.fd], [], [], .05)[0]:
            output.extend(os.read(board.fd, 65536))
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--count', type=int, default=10, help='Repetitions per phase')
    parser.add_argument('--phases', nargs='+', default=['PREPARE', 'START', 'GATE', 'HIGH', 'LOW', 'REFILL'],
                        choices=['PREPARE', 'START', 'GATE', 'HIGH', 'LOW', 'REFILL'])
    parser.add_argument('--thonny-backend', type=Path, help='Installed thonny/plugins/micropython/mp_back.py')
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    helper = thonny_helper(args.thonny_backend) if args.thonny_backend else None
    board = Board(args.port)
    results = []
    try:
        for phase in args.phases:
            for index in range(args.count):
                board.reset()
                if helper:
                    board.execute(helper)
                duration = 20 if phase == 'REFILL' else 800
                command = 'ON' if phase == 'REFILL' else 'HIGH'
                source = SOURCE.replace('yield command, duration', 'yield '+command+', duration')
                submit(board, 'phase=%r\nduration=%r\n' % (phase, duration) + source)
                marker = phase if phase in ('PREPARE', 'START', 'GATE') else 'WAIT'
                output = read_marker(board, (marker+'\r\n').encode())
                delay = (index % 5) * .001 + (.165 if phase == 'REFILL' else
                                              .02 if phase in ('GATE', 'HIGH', 'LOW') else 0)
                time.sleep(delay)
                os.write(board.fd, b'\x03')
                deadline = time.monotonic() + 3
                while not output.endswith(b'\x04>'):
                    if time.monotonic() > deadline:
                        raise TimeoutError(output.decode(errors='replace'))
                    if select.select([board.fd], [], [], .05)[0]:
                        output += os.read(board.fd, 65536)
                assert b'CLOSED\r\n' in output, output.decode(errors='replace')
                assert b'KeyboardInterrupt' in output, output.decode(errors='replace')
                os.write(board.fd, b'\x02')
                assert 'REUSABLE' in board.execute(CHECK)['stdout']
                # Thonny's _clear_repl sends Ctrl-D at the raw prompt.
                os.write(board.fd, b'\r\x01')
                read_until(board.fd, b'raw REPL; CTRL-B to exit\r\n>')
                os.write(board.fd, b'\x04')
                reset = read_until(board.fd, b'raw REPL; CTRL-B to exit\r\n>', timeout=5)
                assert b'soft reboot' in reset, repr(reset)
                os.write(board.fd, b'\x02')
                record = {'phase': phase, 'index': index, 'delay_s': delay,
                          'interrupts': 1, 'closed': True, 'resources_reusable': True,
                          'soft_reset': True, 'output': output.decode(errors='replace')}
                results.append(record)
                args.result.write_text(json.dumps(results, indent=2)+'\n')
                print(json.dumps({k:v for k,v in record.items() if k != 'output'}), flush=True)
    finally:
        board.close()


if __name__ == '__main__':
    main()
