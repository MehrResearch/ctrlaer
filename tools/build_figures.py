#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib>=3.9,<4"]
# ///
"""Regenerate documentation waveforms from the actual finite compiler."""
from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ctrlaer
from ctrlaer import ON, OFF, HIGH, square_wave, sweep, mux, sleep, wait_for, print as emit_print
from ctrlaer_program import compile_program
from tools.docs_figures import waveform_svg as render_svg, build_diagrams
from examples import patterns
from examples.adiabatic import experiment as adiabatic


def main():
    destination = ROOT / "docs" / "assets"
    destination.mkdir(exist_ok=True)
    build_diagrams(destination)

    program = compile_program(patterns.bursts(), freq=40000, names=["GP0"])
    for filename, window in (("first-bursts.svg", None),
                             ("first-bursts-zoom.svg", .1)):
        (destination / filename).write_text(render_svg(program, window_ms=window))
        print(filename)

    def blink():
        for _ in range(3):
            yield HIGH, 500
            yield OFF, 500

    def light():
        yield HIGH,10
        yield OFF,90

    def camera():
        yield OFF,5
        yield HIGH,1
        yield OFF,94

    def sweep_example():
        yield sweep(90000,120000,sweep_ms=2),20
        yield OFF,100

    examples = [
        ('first-levels.svg', blink, dict(freq=1000, names=['LED · GP25'])),
        ('light-camera.svg', lambda:mux([light(),camera()]), dict(n_pins=2,freq=1000,names=['Light · GP0','Camera · GP1'])),
    ]
    for filename,pairs in [
        ('valve.svg',[(HIGH,500),(OFF,500)]),
        ('camera-trigger.svg',[(HIGH,1),(OFF,99)]),
        ('ultrasound.svg',[(ON,2),(OFF,8)]),
        ('atomiser.svg',[(square_wave(100000,duty=.4),20),(OFF,20)]),
        ('sweep-envelope.svg',[(sweep(90000,120000,sweep_ms=2),20),(OFF,100)]),
        ('fast-signal.svg',[(square_wave(500000),1),(OFF,1)]),
    ]:
        examples.append((filename,lambda pairs=pairs:iter(pairs),dict(freq=40000 if filename=='ultrasound.svg' else 1000,names=['GP0'])))
    for filename,factory,options in examples:
        view = 15 if filename == 'light-camera.svg' else None
        (destination/filename).write_text(render_svg(compile_program(factory,clock_hz=125000000,**options),window_ms=view))
        print(filename)

    def vocabulary():
        emit_print('beginning shot')
        yield sweep(90000,120000,sweep_ms=2),20
        yield OFF,1
        sleep(.1)
        wait_for(6,timeout_ms=1000)
        yield square_wave(108050,duty=.4),20

    plans = (
        ('staggered.svg', patterns.staggered, dict(n_pins=2,freq=40000,names=['GP0','GP1']), None),
        ('complementary.svg', patterns.complementary, dict(n_pins=2,names=['GP0','GP1']), .1),
        ('triggered.svg', patterns.triggered, dict(names=['GP0']), None),
        ('adiabatic.svg', sweep_example, dict(names=['GP0']), None),
        ('adiabatic-zoom.svg', sweep_example, dict(names=['GP0']), .1),
        ('group-a.svg', lambda:iter([(square_wave(108050),20)]), dict(names=['GP0']), .05),
        ('group-b.svg', lambda:iter([(square_wave(111500,duty=.4,phase=.25),20)]), dict(names=['GP1']), .05),
        ('prepared.svg', lambda:iter([(ON,20),(OFF,100)]), dict(names=['GP0']), None),
        ('vocabulary.svg', vocabulary, dict(names=['GP0']), None),
        ('capture-reference.svg', lambda:iter([(square_wave(100000),20)]), dict(names=['GP0']), .05),
    )
    for filename, factory, options, window in plans:
        compiled = compile_program(factory, **options)
        (destination / filename).write_text(render_svg(compiled, window_ms=window))
        print(filename)

    def legacy_functions(filename):
        # These two files have pure generator functions but also start hardware
        # at module level. Load only their function definitions for previews.
        tree = ast.parse((ROOT/'examples'/filename).read_text())
        body = [node for node in tree.body if isinstance(node,ast.FunctionDef)]
        namespace = dict(vars(ctrlaer))
        exec(compile(ast.Module(body=body,type_ignores=[]),filename,'exec'),namespace)
        return namespace

    stepper = legacy_functions('stepper.py')
    ultrasonic = legacy_functions('ultrasonic.py')
    legacy = (
        ('legacy-phases.svg', lambda:mux([stepper['phase1'](10),stepper['phase2'](10)]),
         dict(n_pins=2,freq=10000,names=['GP0','GP1']), None),
        ('legacy-ultrasonic.svg', lambda:mux([ultrasonic[name]() for name in
          ('low_freq','phase1','phase2','phase1','phase2')]),
         dict(n_pins=5,freq=40000,names=['GP0','GP1','GP2','GP3','GP4'],window_ms=1010), None),
        ('legacy-ultrasonic-zoom.svg', lambda:mux([ultrasonic[name]() for name in
          ('low_freq','phase1','phase2','phase1','phase2')]),
         dict(n_pins=5,freq=40000,names=['GP0','GP1','GP2','GP3','GP4'],window_ms=.1), None),
        ('legacy-serial.svg', lambda:iter([(ON,100),(OFF,200),(HIGH,150),(OFF,200),(1,100)]),
         dict(freq=5000,names=['GP0']), None),
        ('legacy-timing-zoom.svg', lambda:iter([(ON,200)]),
         dict(freq=100000,names=['GP7'],window_ms=.05), None),
    )
    for filename,factory,options,window in legacy:
        (destination/filename).write_text(render_svg(compile_program(factory,**options),window_ms=window))
        print(filename)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    matplotlib.rcParams['svg.hashsalt'] = 'ctrlaer-docs'
    compiled = compile_program(sweep_example)
    rises = [tick / compiled.clock_hz * 1000 for tick,level in compiled.edges(0) if level]
    first = rises[0]
    midpoints = [(a+b)/2-first for a,b in zip(rises,rises[1:])]
    frequencies = [1/(b-a) for a,b in zip(rises,rises[1:])]  # kHz from milliseconds
    times = [i/100 for i in range(2001)]
    requested = [90+15*(t%4 if t%4<=2 else 4-t%4) for t in times]
    with plt.style.context('default'):
        fig,ax = plt.subplots(figsize=(9,3),layout='constrained')
        fig.patch.set_facecolor('#ffffff')
        ax.set_facecolor('#ffffff')
        ax.plot(times,requested,color='#355cbb',label='Requested frequency')
        ax.plot(midpoints[::20],frequencies[::20],'.',color='#137b67',ms=3,
                label='Compiled period estimates (every 20th cycle)')
        ax.set(xlim=(0,20),ylim=(85,125),xlabel='Time since the square wave started (ms)',
               ylabel='Frequency (kHz)',title='20 ms of output · 2 ms up, 2 ms down · five complete sweeps')
        ax.set_xticks(range(0,21,2))
        ax.grid(alpha=.16)
        ax.legend(loc='lower left',ncol=2,fontsize=9)
        fig.savefig(destination/'sweep-frequency.svg',metadata={'Date':None})
        plt.close(fig)
        print('sweep-frequency.svg')

    def envelope(program, filename, title):
        with plt.style.context('default'):
            fig,axes=plt.subplots(program.n_pins,1,sharex=True,squeeze=False,
                                  figsize=(12,1.5+program.n_pins),layout='constrained')
            fig.patch.set_facecolor('#ffffff')
            for channel,row in enumerate(axes):
                ax=row[0];ax.set_facecolor('#ffffff')
                edges=list(program.edges(channel))
                times=[0]+[t*1000/program.clock_hz for t,_ in edges]+[program.duration_ms]
                levels=[0]+[level for _,level in edges]+[0]
                ax.step(times,levels,where='post',color='#355cbb')
                ax.fill_between(times,levels,step='post',color='#355cbb',alpha=.45)
                ax.set(ylim=(-.15,1.25),yticks=[0,1],yticklabels=['OFF','ON'],ylabel=program.names[channel])
                ax.grid(alpha=.15)
            axes[-1][0].set(xlabel='Nominal waveform time (ms)',xlim=(0,program.duration_ms))
            axes[0][0].set_title(title)
            fig.savefig(destination/filename,metadata={'Date':None})
            plt.close(fig)
            print(filename)

    def timing_envelope():
        for _ in range(5):
            yield HIGH,200
            yield OFF,200
    envelope(compile_program(timing_envelope,freq=100000,names=['GP7 envelope']),
             'legacy-timing.svg','Five 200 ms bursts / 200 ms rests · carrier represented by ON bands')

    constants={}
    for node in ast.parse((ROOT/'examples/blocking.py').read_text()).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try:constants[node.targets[0].id]=ast.literal_eval(node.value)
            except (ValueError,TypeError):pass
    def proportion(reverse=False):
        count=constants['N'];on=constants['on_time']
        for i in range(count):
            time=int(on*(count-1-i if reverse else i)/(count-1))
            for _ in range(constants['n']):
                yield OFF,on-time
                yield HIGH,time
                yield OFF,constants['off_time']
    envelope(compile_program(lambda:mux([proportion(),proportion(True)]),n_pins=2,
                             freq=111500,names=['GP7 envelope','GP8 envelope']),
             'legacy-proportions.svg','50 composition steps · Python reaction/air-control gaps excluded')

    # Matplotlib leaves spaces at path line endings. Keep generated assets
    # reproducible and clean under git diff --check.
    for path in destination.glob('*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')


if __name__ == "__main__":
    main()
