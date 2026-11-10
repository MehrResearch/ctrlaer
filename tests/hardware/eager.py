"""Capture eager output across Python gaps and check ordering; no jumper needed.

GPIO8 is observed internally by capture SM1 while SM0 drives it. GPIO9 is held
HIGH for a level wait. All pads return to input after testing.
"""
from ctrlaer import (CtrlAer, HIGH, LOW, sleep, wait_for, DigitalCapture, Trigger,
                     print as emit_print)
from machine import Pin
from time import sleep_ms
import json

Pin(8,Pin.IN,Pin.PULL_DOWN)
Pin(9,Pin.OUT,value=1)
# The Tiny's deliberately non-real-time Python/preparation gaps can exceed
# 350 ms across this many fragments. Keep the same sample-buffer footprint.
capture=DigitalCapture(1,8,sample_hz=100000,duration_ms=700,trigger=Trigger(8))
controller=CtrlAer(0,8,1)
observed=[]

def program():
    yield HIGH,20
    observed.append(Pin(8).value())
    sleep_ms(5)  # Ordinary Python work must happen after the HIGH command.
    observed.append(Pin(8).value())
    sleep(.005)  # Managed sleep must preserve the last digital level too.
    wait_for(9,level=1,timeout_ms=100)
    yield HIGH,10
    observed.append(Pin(8).value())
    emit_print('between eager fragments')
    wait_for(9,level=1,timeout_ms=100)
    yield LOW,10
    observed.append(Pin(8).value())

try:
    capture.start()
    run=controller.run(program(),mode='eager')
    assert observed==[1,1,1,0],str(observed)
    assert run.state=='done' and Pin(8).value()==0
    assert run.elapsed_us>=50000,str(run.elapsed_us)
    assert [entry['gate'] for entry in run.gate_history]==[0,0,1,1],str(run.gate_history)
    assert len(run.effect_history)==1,str(run.effect_history)
    trace=capture.wait(1000)
    levels=[level for _,level in trace.edges(0,include_initial=True)]
    assert levels==[1,0],str(levels)
    result={'ordered_levels':observed,'captured_levels':levels,'status':run.status()}
    capture.close()

    def broken():
        yield HIGH,2
        raise ValueError('deliberate generator failure')
    try:
        controller.run(broken(),mode='eager',final_mask=0)
        raise AssertionError('expected generator failure')
    except ValueError as error:
        assert str(error)=='deliberate generator failure'
        assert Pin(8).value()==0
        result['generator_error_final_mask']='passed'

    run=controller.run([(HIGH,2)],mode='eager',final_mask=1)
    assert Pin(8).value()==1 and run.state=='done'
    result['success_final_mask']='passed'

    def abort_between_fragments():
        yield HIGH,2
        controller.abort()
        yield HIGH,10
        raise AssertionError('generator advanced after eager cancellation')
    run=controller.run(abort_between_fragments(),mode='eager',final_mask=0)
    assert run.state=='aborted' and Pin(8).value()==0,run.state
    result['abort_between_fragments']='passed'

    def close_between_fragments():
        yield HIGH,2
        controller.close()
        yield HIGH,10
        raise AssertionError('generator advanced after eager close')
    run=controller.run(close_between_fragments(),mode='eager',final_mask=0)
    assert run.closed and run.state=='aborted' and Pin(8).value()==0,run.state
    result['close_between_fragments']='passed'

    def effects_before_first_command():
        emit_print('before first eager command')
        wait_for(9,level=1,timeout_ms=100)
        sleep(.001)
        yield HIGH,2
    run=controller.run(effects_before_first_command(),mode='eager')
    assert run.state=='done' and len(run.gate_history)==2
    assert len(run.effect_history)==1
    result['effects_before_first_command']='passed'

    def early_abort():
        controller.abort()
        yield LOW,2
    run=controller.run(early_abort(),mode='eager',final_mask=1)
    assert run.state=='aborted' and Pin(8).value()==1
    result['abort_before_first_command']='passed'

    def initially_triggered():
        wait_for(9,level=1,timeout_ms=100)
        yield HIGH,2
    run=controller.run(initially_triggered(),mode='eager',trigger=Trigger(9,level=1))
    assert run.state=='done'
    assert [event['gate'] for event in run.gate_history]==[0,0,1,1]
    result['initial_trigger']='passed'

    run=controller.run([],mode='eager',final_mask=1)
    assert run.state=='done' and Pin(8).value()==1
    result['empty_program_final_mask']='passed'
finally:
    capture.close()
    controller.close()
    Pin(8,Pin.IN)
    Pin(9,Pin.IN)
print(json.dumps(result))
