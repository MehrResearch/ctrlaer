"""Third-PIO execution and atomic cross-PIO start on an idle Pico Plus 2."""
from ctrlaer import CtrlAer,pulse,Group,DigitalCapture,Trigger
from machine import Pin
import sys,json

assert 'RP2350' in sys.implementation._machine
a,b=CtrlAer(0,8,1),CtrlAer(8,9,1)
capture=None
try:
    ra=a.prepare([(pulse(100000),1)])
    rb=b.prepare([(pulse(40000),1)])
    capture=DigitalCapture(4,8,2,sample_hz=25000000,duration_ms=1.2,trigger=Trigger(8))
    capture.start()
    Group([ra,rb]).start().wait(1000)
    trace=capture.wait(1000)
    assert next(trace.levels())==3
    ca=trace.compare(ra.program,channels={0:0},tolerance_ns=80)
    cb=trace.compare(rb.program,channels={0:1},tolerance_ns=80)
    assert ca['passed'] and cb['passed'],(ca,cb)
    print(json.dumps({'third_pio':True,'atomic_cross_pio':True,'comparisons':[ca,cb]}))
finally:
    if capture:capture.close()
    a.close();b.close()
    Pin(8,Pin.IN);Pin(9,Pin.IN)
