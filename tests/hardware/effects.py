"""Check that managed generator effects are compiled, timed and replayed."""
from ctrlaer import CtrlAer,HIGH,LOW,sleep,print as event_print
from machine import Pin
import json

controller=CtrlAer(0,8,1,freq=100000)
try:
    def experiment():
        event_print('effect before')
        yield HIGH,1
        sleep(.01)
        event_print('effect after')
        yield LOW,1
    run=controller.prepare(experiment)
    assert run.state=='prepared' and len(run.effect_history)==0
    assert run.program.summary()['requested_duration_ms']==12
    run.start().wait(1000)
    assert len(run.effect_history)==2,run.status()
    assert [event['text'] for event in run.effect_history]==['effect before\n','effect after\n']
    assert run.elapsed_us>=12000,run.elapsed_us
    assert Pin(8).value()==0
    print(json.dumps({'effects_replayed':2,'elapsed_us':run.elapsed_us,'generator_api':True}))
finally:
    controller.close()
    Pin(8,Pin.IN)
