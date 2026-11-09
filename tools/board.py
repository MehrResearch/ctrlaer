#!/usr/bin/env python3
"""Deploy CtrlAer modules and run a MicroPython check over POSIX USB serial.

Uses the raw REPL, not a firmware flash. --deploy overwrites /lib/ctrlaer*.py
modules from this checkout. Existing unrelated files are left alone.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import termios
import time


def read_until(fd, marker, timeout=10):
    deadline = time.monotonic() + timeout
    result = bytearray()
    while not result.endswith(marker):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(repr(bytes(result[-2000:])))
        if select.select([fd], [], [], remaining)[0]:
            try:
                data = os.read(fd, 65536)
                if not data:
                    raise ConnectionError('USB serial disconnected')
                result.extend(data)
            except BlockingIOError:
                pass
    return bytes(result)


class Board:
    def __init__(self, port):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.original = termios.tcgetattr(self.fd)
        self.changed = bool(self.original[3] & (termios.ICANON | termios.ECHO | termios.ISIG))
        if self.changed:
            import tty
            try:
                tty.setraw(self.fd)
            except BaseException:
                os.close(self.fd)
                raise

    def drain(self):
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 65536)

    def reset(self):
        os.write(self.fd, b'\r\x03\x03\x02')
        time.sleep(.1)
        self.drain()
        os.write(self.fd, b'\x04')
        return read_until(self.fd, b'>>> ').decode(errors='replace')

    def execute(self, source, timeout=15):
        os.write(self.fd, b'\r\x03\x03')
        time.sleep(.05)
        self.drain()
        os.write(self.fd, b'\r\x01')
        read_until(self.fd, b'raw REPL; CTRL-B to exit\r\n>')
        try:
            data = source.encode()
            for offset in range(0, len(data), 128):
                os.write(self.fd, data[offset:offset + 128])
                time.sleep(.003)
            os.write(self.fd, b'\x04')
            response = read_until(self.fd, b'\x04>', timeout)
            if not response.startswith(b'OK'):
                raise RuntimeError(repr(response[:1000]))
            stdout, stderr = response[2:-2].split(b'\x04', 1)
            result = {'stdout': stdout.decode(errors='replace'),
                      'stderr': stderr.decode(errors='replace')}
            if result['stderr']:
                raise RuntimeError(result['stdout'] + result['stderr'])
            return result
        finally:
            os.write(self.fd, b'\x02')

    def upload(self, path, remote=None):
        remote = '/lib/' + path.name if remote is None else remote
        self.execute("import os\ntry:\n os.mkdir('/lib')\nexcept OSError:\n pass\n"
                     "f = open(%r, 'wb')" % remote)
        for offset in range(0, path.stat().st_size, 1024):
            with path.open('rb') as stream:
                stream.seek(offset)
                data = stream.read(1024)
            self.execute('f.write(%r)' % data)
        self.execute('f.close()\nos.sync()')

    def download(self, remote, destination):
        size = int(self.execute("import os\nprint(os.stat(%r)[6])" % remote)['stdout'].strip())
        self.execute("import binascii\nf = open(%r, 'rb')" % remote)
        try:
            with destination.open('wb') as stream:
                for _ in range(0, size, 768):
                    encoded = self.execute("print(binascii.b2a_base64(f.read(768)).decode().strip())")['stdout']
                    stream.write(base64.b64decode(encoded.strip()))
        finally:
            self.execute('f.close()')
        if destination.stat().st_size != size:
            raise RuntimeError('download size mismatch')

    def close(self):
        if self.changed:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
        os.close(self.fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--get', nargs=2, metavar=('DEVICE_PATH', 'LOCAL_PATH'))
    parser.add_argument('script', type=Path, nargs='?')
    args = parser.parse_args()
    board = Board(args.port)
    try:
        print(board.reset().strip())
        if args.deploy:
            root = Path(__file__).resolve().parent.parent
            for path in sorted(root.glob('ctrlaer*.py')):
                board.upload(path)
                print('DEPLOYED', path.name)
            board.reset()
        if args.script:
            result = board.execute(args.script.read_text(), args.timeout)
            print(result['stdout'])
            if args.result:
                args.result.write_text(json.dumps(result, indent=2) + '\n')
        if args.get:
            destination = Path(args.get[1])
            board.download(args.get[0], destination)
            print('DOWNLOADED', destination)
    finally:
        board.close()


if __name__ == '__main__':
    main()
