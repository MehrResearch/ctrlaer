"""Resource/fault isolation and finite capture reuse on idle GP8/GP9."""
from ctrlaer import CtrlAer,HIGH,LOW,pulse,compile_program,DigitalCapture,Trigger,Group
from ctrlaer_runtime import UnderrunError,ExecutionTimeoutError
from ctrlaer_capture import CaptureOverrunError
from machine import Pin,freq
from time import sleep_ms
import json

a,b=CtrlAer(0,8,1),CtrlAer(1,9,1)
capture=None
result={}
try:
    ra=a.prepare([(HIGH,20)])
    for sm,pin in ((0,9),(1,8)):
        other=CtrlAer(sm,pin,1)
        try:
            other.prepare([(HIGH,1)])
            raise AssertionError('ownership conflict accepted')
        except RuntimeError:
            pass
        finally:other.close()
    result['ownership_conflicts_rejected']=True
    # Keep this peer active beyond ordinary interpreter/abort-cleanup latency.
    ra=a.prepare([(HIGH,200)])
    rb=b.prepare([(pulse(40000),20)])
    rb._tx.active(0)
    group=Group([ra,rb])
    group.start()
    sleep_ms(15)
    assert rb.state=='error' and ra.state=='aborted',(ra.status(),rb.status())
    try:
        group.wait(1000)
        raise AssertionError('group feeder failure accepted')
    except UnderrunError:
        assert ra.state=='aborted' and Pin(8).value()==0,(ra.status(),rb.status(),Pin(8).value())
    result['group_fault_aborts_peers']=True
    group.close()
    b.close()
    Pin(9,Pin.OUT,value=0)
    capture=DigitalCapture(4,8,sample_hz=1000000,duration_ms=1,trigger=Trigger(9))
    try:
        capture.start().wait(2)
        raise AssertionError('untriggered capture did not time out')
    except ExecutionTimeoutError:
        assert capture.state=='aborted'
    capture.close();capture=None
    result['capture_timeout']=True
    for rate in (.1,freq()/140000,freq()):
        try:
            DigitalCapture(4,8,sample_hz=rate,samples=1)
            raise AssertionError('impossible capture clock accepted')
        except ValueError:pass
    result['capture_clock_validation']=True
    capture=DigitalCapture(4,8,sample_hz=1000000,duration_ms=100)
    capture._dma.active(0)
    try:
        capture.start().wait(1000)
        raise AssertionError('stalled capture was accepted')
    except CaptureOverrunError:
        assert capture.state=='error' and capture.trace.metadata['overrun']
    capture.close();capture=None
    result['injected_capture_overrun']=True
    ra=a.prepare([(pulse(100000),.5)])
    capture=DigitalCapture(4,8,sample_hz=1000000,duration_ms=1,trigger=Trigger(8))
    for index in range(2):
        if index:
            ra.rearm();capture.arm()
        capture.start();ra.start().wait(1000)
        trace=capture.wait(1000)
        assert trace.compare(ra.program,tolerance_ns=2000)['passed']
    result['capture_reuse']=True
    print(json.dumps(result))
finally:
    if capture:capture.close()
    a.close();b.close()
    Pin(8,Pin.IN);Pin(9,Pin.IN)
