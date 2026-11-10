#!/usr/bin/env python3
"""Verify a GPIO/GND link in both directions using independent RP2 clocks."""
import argparse
from array import array
import base64
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ctrlaer import compile_program, HIGH, OFF, pulse, chirp
from ctrlaer_capture import Trace
from ctrlaer_records import save_record, fingerprint
from ctrlaer_tools import program_to_dict, program_from_dict
spec=importlib.util.spec_from_file_location('board', ROOT/'tools/board.py')
board_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(board_module)
Board=board_module.Board

WAVEFORM = """
def experiment(chirp_ms=20,sweep_ms=2,start_hz=90000,stop_hz=120000):
    yield HIGH, .1
    yield OFF, .2
    yield pulse(10000,duty=.25), 2
    yield OFF, .3
    yield pulse(108050,duty=.4,phase=.25), 2
    yield OFF, .3
    yield chirp(start_hz,stop_hz,sweep_ms=sweep_ms), chirp_ms
    yield OFF, .2
"""
exec(WAVEFORM)

INITIALISE = """
from machine import Pin, Timer, freq, mem32
import sys,json
Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
role_timer=None
is_tiny='Tiny 2040' in sys.implementation._machine
if is_tiny:
    rgb=[Pin(number,Pin.OUT,value=1) for number in (18,19,20)]
else:
    user_led=Pin('LED',Pin.OUT,value=0)
def set_role(role):
    global role_timer
    if role_timer is not None:
        role_timer.deinit()
        role_timer=None
    if not role_leds:
        return
    if is_tiny:
        colour={'sender':(0,0,1),'receiver':(0,1,0),'error':(1,0,0)}[role]
        for led,value in zip(rgb,colour):led.value(1-value)
    else:
        user_led.value(1)
        if role!='sender':
            role_timer=Timer(-1)
            role_timer.init(period=250 if role=='receiver' else 75,mode=Timer.PERIODIC,callback=lambda timer:user_led.toggle())
print(json.dumps({'board':sys.implementation._machine,'firmware':sys.version,'clock_hz':freq(),'led':str(rgb) if is_tiny else str(user_led)}))
"""

PREPARE_TX = """
from ctrlaer import CtrlAer,HIGH,OFF,pulse,chirp
set_role('sender')
""" + WAVEFORM + """
controller=CtrlAer(0,link_pin,1,freq=100000)
run=controller.prepare(lambda:experiment(chirp_ms,sweep_ms,start_hz,stop_hz),mode=execution_mode)
if execution_mode=='finite':
    from ctrlaer_tools import program_to_dict
    from ctrlaer_records import fingerprint
    print(json.dumps({'program':program_to_dict(run.program,include_words=False),'sha256':fingerprint(run.program.words)}))
else:
    print(json.dumps({'state':run.state,'mode':execution_mode}))
"""

PREPARE_RX = """
from ctrlaer import DigitalCapture,Trigger
from ctrlaer_records import fingerprint
Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
set_role('receiver')
capture=DigitalCapture(0,link_pin,sample_hz=capture_hz,duration_ms=capture_ms,trigger=Trigger(link_pin))
capture.start()
print(json.dumps({'state':capture.state,'sample_hz':capture.sample_hz,'samples':capture.samples}))
"""

FINISH_TX = """
try:
    run.start().wait(1000)
    print(json.dumps(run.status()))
finally:
    controller.close()
    Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
"""
FINISH_RX = """
try:
    trace=capture.wait(1000)
    print(json.dumps({'summary':trace.summary(),'words':len(trace.words),'sha256':fingerprint(trace.words)}))
finally:
    capture.close()
    Pin(link_pin,Pin.IN,Pin.PULL_DOWN)
"""


def execute(board, source, timeout=60):
    response=board.execute(source,timeout)
    return json.loads(response['stdout'])


def fetch_words(board,expression,count):
    raw=bytearray()
    board.execute('import binascii')
    for start in range(0,count,192):
        encoded=board.execute('print(binascii.b2a_base64(memoryview(%s)[%d:%d]).decode().strip())'%(expression,start,start+192))['stdout']
        raw.extend(base64.b64decode(encoded.strip()))
    words=array('I');words.frombytes(raw)
    if sys.byteorder!='little':words.byteswap()
    return words


def analyse(program,trace):
    expected=list(program.edges(0))
    observed=list(trace.edges(0,True))
    if len(expected)!=len(observed) or [level for _,level in expected]!=[level for _,level in observed]:
        return {'passed':False,'expected_transitions_including_initial':len(expected),'observed_transitions_including_initial':len(observed),'direct_comparison':trace.compare(program,tolerance_ns=2000)}
    xs=[tick*1e9/program.clock_hz for tick,_ in expected[1:]]
    ys=[sample*1e9/trace.sample_hz for sample,_ in observed[1:]]
    xmean,ymean=sum(xs)/len(xs),sum(ys)/len(ys)
    scale=sum((x-xmean)*(y-ymean) for x,y in zip(xs,ys))/sum((x-xmean)**2 for x in xs)
    origin=ymean-scale*xmean
    residual=max(abs(y-(origin+scale*x)) for x,y in zip(xs,ys))
    ppm=(scale-1)*1e6
    direct=trace.compare(program,tolerance_ns=2000)
    result={'passed':direct['passed'] and abs(ppm)<=100 and residual<=200,
            'observed_transitions_including_initial':len(observed),
            'rising_edges_including_initial':sum(level for _,level in observed),
            'relative_clock_difference_ppm':ppm,'max_residual_after_clock_fit_ns':residual,
            'clock_fit_origin_ns':origin,'direct_comparison':direct,
            'limits':{'unscaled_tolerance_ns':2000,'relative_clock_difference_ppm':100,'fitted_residual_ns':200}}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tiny-port',required=True)
    parser.add_argument('--pico-port',required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    parser.add_argument('--gpio',type=int,choices=range(8),default=6,help='shared GPIO number (default: 6)')
    parser.add_argument('--direction',choices=('both','tiny-to-pico','pico-to-tiny'),default='both')
    parser.add_argument('--mode',choices=('finite','buffered'),default='finite')
    parser.add_argument('--chirp-ms',type=float,default=20,help='Chirp ON duration, independent of the sweep leg')
    parser.add_argument('--sweep-ms',type=float,default=2,help='One-way chirp sweep duration (default: 2 ms)')
    parser.add_argument('--start-hz',type=float,default=90000,help='Chirp starting frequency (default: 90000 Hz)')
    parser.add_argument('--stop-hz',type=float,default=120000,help='Chirp turnaround frequency (default: 120000 Hz)')
    parser.add_argument('--sample-hz',type=float,default=10000000,help='Lower to e.g. 4000000 for a 100 ms chirp on the Tiny')
    parser.add_argument('--no-leds',action='store_true',help='Keep board LEDs off during unattended tests')
    args=parser.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    boards={}
    devices={}
    checks=[]
    try:
        for key,port in (('tiny',args.tiny_port),('pico',args.pico_port)):
            board=Board(port);boards[key]=board
            board.reset()
            devices[key]=execute(board,'link_pin=%d\nrole_leds=%r\n'%(args.gpio,not args.no_leds)+INITIALISE)
            print(key,devices[key],flush=True)
        assert 'Tiny 2040' in devices['tiny']['board']
        assert 'RP2350' in devices['pico']['board']
        directions=(('tiny','pico'),('pico','tiny')) if args.direction=='both' else (tuple(args.direction.split('-to-')),)
        for sender,receiver in directions:
            name=f'{sender}-to-{receiver}'
            print('PREPARING',name,flush=True)
            tx,rx=boards[sender],boards[receiver]
            # Both link pads are inputs before enabling the next transmitter.
            for board in boards.values():execute(board,"Pin(link_pin,Pin.IN,Pin.PULL_DOWN)\nprint('{}')")
            host_program=compile_program(lambda:experiment(args.chirp_ms,args.sweep_ms,args.start_hz,args.stop_hz),freq=100000,
                                         clock_hz=devices[sender]['clock_hz'],max_words=1000000)
            header=execute(tx,'execution_mode=%r\nchirp_ms=%r\nsweep_ms=%r\nstart_hz=%r\nstop_hz=%r\n'%
                           (args.mode,args.chirp_ms,args.sweep_ms,args.start_hz,args.stop_hz)+PREPARE_TX)
            source=None
            if args.mode=='finite':
                compiled=header['program']
                source=fetch_words(tx,'run.program.words',compiled['buffer_bytes']//4)
                if fingerprint(source)!=header['sha256']:
                    raise RuntimeError('transmitted program download hash mismatch')
                expected=program_from_dict(compiled,source)
                assert host_program.pulse_counts==expected.pulse_counts
                assert host_program.duration_ticks==expected.duration_ticks
            else:
                assert header['state']=='prepared'
                expected=host_program
            capture_ms=math.ceil(expected.duration_ms+1)
            armed=execute(rx,'capture_hz=%r\ncapture_ms=%r\n'%(args.sample_hz,capture_ms)+PREPARE_RX)
            assert armed['state']=='running'
            status=execute(tx,FINISH_TX)
            assert status['state']=='done' and not status['underrun']
            received=execute(rx,FINISH_RX)
            words=fetch_words(rx,'trace.words',received['words'])
            if fingerprint(words)!=received['sha256']:
                raise RuntimeError('capture download hash mismatch')
            info=received['summary']
            trace=Trace(words,info['n_pins'],info['samples'],info['sample_hz'],info['names'],info['metadata'])
            comparison=analyse(expected,trace)
            if source is not None:
                comparison['host_and_device_compilation_identical']=source==host_program.words
            record_path=args.out_dir/f'{name}.json'
            save_record(record_path,expected,trace=trace,device=devices[sender],
                        results={'comparison':comparison,'transmitter_status':status},
                        metadata={'test':'cross-board-gpio%d'%args.gpio,'sender':devices[sender],
                                  'receiver':devices[receiver],'gpio':args.gpio,'common_ground':True,
                                  'execution_mode':args.mode,'chirp_ms':args.chirp_ms,
                                  'sweep_ms':args.sweep_ms,
                                  'start_hz':args.start_hz,'stop_hz':args.stop_hz,
                                  'roles':({'leds':'off'} if args.no_leds else
                                           {'tiny_rgb':'blue=sender, green=receiver','pico_led':'steady=sender, blinking=receiver'})})
            with (args.out_dir/f'{name}.vcd').open('w') as stream:trace.write_vcd(stream)
            checks.append({'direction':name,'record':record_path.name,**comparison})
            (args.out_dir/'results.json').write_text(json.dumps({'gpio':args.gpio,'devices':devices,'checks':checks},indent=2)+'\n')
            brief={key:value for key,value in comparison.items() if key!='direct_comparison'}
            brief['mismatches']=comparison['direct_comparison']['mismatches']
            print(name,json.dumps(brief),flush=True)
            # Return retained capture memory before this board becomes sender.
            execute(rx,"del capture,trace\nimport gc\ngc.collect()\nprint('{}')")
            execute(tx,"del run,controller\nimport gc\ngc.collect()\nprint('{}')")
        passed=sum(check['passed'] for check in checks)
        indicators='LEDs off' if args.no_leds else 'last role LEDs retained'
        print('%d/%d directions passed; %s, GPIO%d inputs'%(passed,len(checks),indicators,args.gpio),flush=True)
    except BaseException:
        for board in boards.values():
            try:board.execute("set_role('error')\nPin(link_pin,Pin.IN,Pin.PULL_DOWN)")
            except Exception:pass
        raise
    finally:
        for board in boards.values():
            try:
                board.execute("if 'controller' in globals():controller.close()\nif 'capture' in globals():capture.close()\nPin(link_pin,Pin.IN,Pin.PULL_DOWN)")
            except Exception:pass
            board.close()
    return 0 if all(check['passed'] for check in checks) else 1

if __name__=='__main__':raise SystemExit(main())
