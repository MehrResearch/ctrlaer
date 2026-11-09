"""Repeated immediately satisfied gates, including RP2350 PSRAM allocations."""
from ctrlaer import CtrlAer,HIGH,Trigger
from ctrlaer_runtime import GateTimeoutError
from machine import Pin
import json,gc,uctypes,sys
from array import array

Pin(9,Pin.OUT,value=1)
c=CtrlAer(0,8,1)
held=[]
psram_runs=0
try:
    for iteration in range(30):
        if iteration%3==0:gc.collect()
        run=c.prepare([(HIGH,1)],trigger=Trigger(9,timeout_ms=1))
        try:
            run.start().wait(1000)
            raise AssertionError('already high input counted as a new rising edge')
        except GateTimeoutError:pass
        run=c.prepare([(HIGH,1)],trigger=Trigger(9,level=1,timeout_ms=20))
        if 'Pico Plus 2' in sys.implementation._machine:
            # Retain exact copies until one lands in external RAM. Explicitly
            # rearm its DMA from that copy so GC policy/heap holes cannot turn
            # this into an accidental SRAM-only test.
            for _ in range(5000):
                candidate=array('I',run.program.words)
                held.append(candidate)
                if 0x11000000<=uctypes.addressof(candidate)<0x12000000:
                    run.program.words=candidate
                    run.rearm()
                    break
            else:raise AssertionError('could not allocate a PSRAM test buffer')
        address=uctypes.addressof(run.program.words)
        psram_runs+=int(0x11000000<=address<0x12000000)
        run.start().wait(1000)
        assert run.state=='done' and not run.status()['underrun'],run.status()
    if 'Pico Plus 2' in sys.implementation._machine:assert psram_runs>0,psram_runs
    print(json.dumps({'ready_gate_iterations':30,'psram_runs':psram_runs,'passed':True}))
finally:
    c.close()
    Pin(8,Pin.IN);Pin(9,Pin.IN)
