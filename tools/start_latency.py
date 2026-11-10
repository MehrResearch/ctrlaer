#!/usr/bin/env python3
"""Measure cold first-output latency electrically over a GPIO/GND board link.

The sender emits a 20 us marker before importing CtrlAer. The peer captures the
marker and first real waveform edge at 1 MS/s. Both boards' LEDs stay off.
"""
import argparse
from array import array
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.board import Board
from ctrlaer_capture import Trace

QUIET = """
from machine import Pin
import sys,json
Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
if 'Tiny 2040' in sys.implementation._machine:
 for number in (18,19,20):Pin(number,Pin.OUT,value=1)
else:Pin(25,Pin.OUT,value=0)
print(json.dumps({'board':sys.implementation._machine,'firmware':sys.version}))
"""
RECEIVE = """
from ctrlaer import DigitalCapture,Trigger
capture=DigitalCapture(0,link_pin,sample_hz=capture_hz,duration_ms=550,
                       trigger=Trigger(link_pin),max_bytes=80000)
capture.start()
print(json.dumps({'state':capture.state}))
"""
SEND = """
from time import ticks_us,ticks_diff,sleep_us,sleep_ms
signal=Pin(link_pin,Pin.OUT,value=0)
signal.value(1)
sleep_us(20)
signal.value(0)
begin=ticks_us()
from ctrlaer import CtrlAer,sweep
controller=CtrlAer(0,link_pin,1)
software_start=None
try:
 if selected_mode=='eager':
  run=controller.run([(sweep(90000,120000,sweep_ms=selected_sweep_ms),20)],mode='eager')
 else:
  duration=60000 if selected_mode=='buffered' else 20
  run=controller.prepare([(sweep(90000,120000,sweep_ms=selected_sweep_ms),duration)],mode=selected_mode)
  run.start()
  software_start=ticks_diff(ticks_us(),begin)
  if selected_mode=='buffered':
   sleep_ms(20)
   run.abort()
  else:run.wait(1000)
 returned=ticks_diff(ticks_us(),begin)
 status=run.status()
finally:
 controller.close()
 Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
print(json.dumps({'software_prepare_start_us':software_start,
                  'call_return_us':returned,'status':status}))
"""
FINISH = """
try:
 trace=capture.wait(1000)
 print(json.dumps({'summary':trace.summary(),'words':len(trace.words)}))
finally:
 capture.close()
 Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
"""


def execute(board, source):
    return json.loads(board.execute(source, timeout=60)['stdout'])


def fetch_trace(board, count):
    raw = bytearray()
    board.execute('import binascii')
    for start in range(0, count, 192):
        source = 'print(binascii.b2a_base64(memoryview(trace.words)[%d:%d]).decode().strip())' % (start, start+192)
        raw.extend(base64.b64decode(board.execute(source)['stdout'].strip()))
    result = array('I')
    result.frombytes(raw)
    if sys.byteorder != 'little':
        result.byteswap()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tiny-port', required=True)
    parser.add_argument('--pico-port', required=True)
    parser.add_argument('--gpio', type=int, choices=range(8), default=6)
    parser.add_argument('--mode', choices=('all','finite','buffered','eager'), default='all')
    parser.add_argument('--sweep-ms', type=float, default=2)
    parser.add_argument('--sample-hz', type=int, choices=(250000,1000000),default=1000000,
                        help='250000 reduces download time; default 1000000 gives 1 us sampling')
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    boards = {}
    checks = []
    try:
        for name,port in (('tiny',args.tiny_port),('pico',args.pico_port)):
            boards[name] = Board(port)
        modes = ('finite','buffered','eager') if args.mode=='all' else (args.mode,)
        for mode in modes:
            for sender,receiver in (('tiny','pico'),('pico','tiny')):
                devices = {}
                for name,board in boards.items():
                    board.reset()
                    devices[name] = execute(board, 'link_pin=%d\n'%args.gpio + QUIET)
                tx,rx = boards[sender],boards[receiver]
                execute(rx,'capture_hz=%r\n'%args.sample_hz+RECEIVE)
                sent = execute(tx, 'selected_mode=%r\nselected_sweep_ms=%r\n'%(mode,args.sweep_ms) + SEND)
                received = execute(rx,FINISH)
                info = received['summary']
                trace = Trace(fetch_trace(rx,received['words']),1,info['samples'],
                              info['sample_hz'],metadata=info['metadata'])
                transitions = iter(trace.edges(0,include_initial=True))
                initial = next(transitions,None)
                fall = next(transitions,None)
                rise = next(transitions,None)
                valid = (initial is not None and initial[1]==1 and fall is not None and
                         fall[1]==0 and rise is not None and rise[1]==1)
                marker_us = fall[0]*1e6/trace.sample_hz if valid else None
                latency_us = (rise[0]-fall[0])*1e6/trace.sample_hz if valid else None
                name = f'{sender}-to-{receiver}-{mode}'
                with (args.out_dir/(name+'.vcd')).open('w') as stream:
                    trace.write_vcd(stream)
                check = {'sender':sender,'receiver':receiver,'mode':mode,
                         'sweep_ms':args.sweep_ms,
                         'device':devices[sender],'marker_us':marker_us,
                         'physical_first_output_us':latency_us,
                         'limit_us':500000,'sample_hz':trace.sample_hz,**sent,
                         'passed':valid and 5<=marker_us<=100 and latency_us<500000}
                checks.append(check)
                (args.out_dir/'results.json').write_text(json.dumps(checks,indent=2)+'\n')
                print(name,json.dumps(check),flush=True)
        return 0 if all(check['passed'] for check in checks) else 1
    finally:
        for board in boards.values():
            try:
                board.execute("if 'controller' in globals():controller.close()\n"
                              "if 'capture' in globals():capture.close()\n"
                              "Pin(link_pin,Pin.IN,Pin.PULL_DOWN)")
            except Exception:
                pass
            board.close()


if __name__=='__main__':
    raise SystemExit(main())
