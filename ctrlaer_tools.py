"""Generator analysis, exact compiled-buffer interchange and parameter scans."""
from array import array
from ctrlaer_program import CompiledProgram, compile_program, integer, number


def program_to_dict(program, include_words=True):
    program.validate()
    result = program.summary()
    result.update({"schema": "ctrlaer.program/v1", "requested_ticks": program.requested_ticks})
    if include_words:
        result["words"] = list(program.words)
    return result


def program_from_dict(data, words=None):
    if data.get("schema") != "ctrlaer.program/v1":
        raise ValueError("unsupported compiled-program schema")
    n_pins = integer(data["n_pins"], "n_pins", 1, 16)
    names = list(data['names'])
    if len(names) != n_pins or any(not isinstance(name, str) for name in names):
        raise ValueError("names must contain one string per channel")
    source = data["words"] if words is None else words
    packed = array("I")
    for word in source:
        packed.append(integer(word, "packed word", 0, 0xffffffff))
    duration = integer(data["duration_ticks"], "duration_ticks", 0, 0x7fffffffffffffff)
    quantisation = number(data["max_edge_quantisation_ns"], "quantisation")
    requested = number(data.get("requested_ticks", duration), "requested ticks")
    program = CompiledProgram(packed, data["clock_hz"], n_pins, names, duration,
                               data["gates"], data["pulse_counts"], data["final_mask"],
                               quantisation, dict(data["parameters"]), bool(data["windowed"]),
                               requested, data.get("control_overhead_ticks", 0),
                               data.get('events'), data.get('annotations'), _u32=True)
    return program.validate()


def parameter_scan(factory, axes, fixed=None, max_trials=10000, **compile_options):
    """Compile fresh generators from a Python factory in sorted-axis order."""
    if not callable(factory):
        raise ValueError("scans need a generator factory, not an exhausted iterator")
    integer(max_trials, "max_trials", 1, 1000000000)
    names = sorted(axes)
    if not names:
        raise ValueError("a scan needs at least one axis")
    if set(names) & set(fixed or {}):
        raise ValueError("fixed parameters overlap scan axes")
    choices, trials = [], 1
    for name in names:
        values = list(axes[name])
        if not values:
            raise ValueError("scan axes must not be empty")
        choices.append(values)
        trials *= len(values)
        if trials > max_trials:
            raise ValueError("scan exceeds max_trials")
    def visit(depth, binding):
        if depth == len(names):
            yield dict(binding)
        else:
            for value in choices[depth]:
                binding[names[depth]] = value
                yield from visit(depth + 1, binding)
    for index, binding in enumerate(visit(0, dict(fixed or {}))):
        compiled = compile_program(factory, parameters=binding, **compile_options)
        yield index, compiled.parameters, compiled


def _escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', '&quot;')


def render_svg(program, window_ms=None, width=1200, max_edges=10000):
    """Render the exact compiled digital timeline; no recipe/generator is run."""
    integer(width, "width", 400, 10000)
    integer(max_edges, "max_edges", 1, 10000000)
    if window_ms is not None:
        number(window_ms, "preview window", strict=True)
    duration = program.duration_ticks if window_ms is None else min(
        program.duration_ticks, window_ms * program.clock_hz / 1000)
    duration = max(duration, 1)
    height = 150 + program.n_pins * 65
    left, right = 150, width - 30
    def x(tick):
        return left + tick * (right - left) / duration
    elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' %
                (width, height, width, height), '<rect width="100%" height="100%" fill="#101827"/>',
                '<g font-family="sans-serif" font-size="13" fill="#e6edf7">',
                '<text x="24" y="30" font-size="20">CtrlAer — compiled generator output</text>',
                '<text x="24" y="54">%.6g ms · %d Hz clock · %d bytes · edge quantisation ≤ %.3g ns</text>' %
                (program.duration_ms, program.clock_hz, len(program.words) * 4, program.quantisation_ns)]
    if program.gates:
        elements.append('<text x="24" y="76" fill="#ffca70">Nominal times only: external gate waits are additional.</text>')
    count = 0
    for channel, name in enumerate(program.names):
        y = 110 + channel * 65
        elements.append('<text x="24" y="%d">%s</text>' % (y, _escape(name)))
        elements.append('<text x="24" y="%d" fill="#93a5bc">%d pulses</text>' % (y + 19, program.pulse_counts[channel]))
        path = ['M %.3f %d' % (left, y + 20)]
        level = 0
        for tick, next_level in program.edges(channel):
            if tick > duration:
                break
            count += 1
            if count > max_edges:
                raise ValueError("preview exceeds max_edges; choose a shorter window")
            path.append('H %.3f V %d' % (x(tick), y - 8 if next_level else y + 20))
            level = next_level
        path.append('H %.3f' % right)
        elements.append('<path d="%s" fill="none" stroke="#64d8c5" stroke-width="1.5"/>' % ' '.join(path))
    for gate in program.gates:
        if gate['nominal_tick'] <= duration:
            xx = x(gate['nominal_tick'])
            elements.append('<path d="M %.3f 90 V %d" stroke="#ffca70" stroke-dasharray="4 4"/>' % (xx, height - 40))
    for effect in program.annotations:
        if effect['nominal_tick'] <= duration:
            label = effect.get('text', 'sleep').strip()
            elements.append('<circle cx="%.3f" cy="84" r="3" fill="#ffca70"><title>%s</title></circle>' %
                            (x(effect['nominal_tick']), _escape(label)))
    for mark in range(6):
        tick = duration * mark / 5
        elements.append('<text x="%.3f" y="%d" text-anchor="middle">%.6g ms</text>' %
                        (x(tick), height - 17, tick * 1000 / program.clock_hz))
    elements.append('</g></svg>')
    return '\n'.join(elements)


def _load_factory(reference):
    import importlib
    import importlib.util
    from pathlib import Path
    module_name, separator, function = reference.partition(':')
    if not separator or not function:
        raise ValueError("use module:function or path.py:function")
    if module_name.endswith('.py'):
        path = Path(module_name).resolve()
        spec = importlib.util.spec_from_file_location('ctrlaer_experiment', str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    factory = getattr(module, function)
    if not callable(factory):
        raise ValueError("selected program is not a generator factory")
    return factory


def main(argv=None):
    import argparse, json
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Analyse, preview and scan CtrlAer generators")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ('preview', 'scan'):
        sub = commands.add_parser(command)
        sub.add_argument('program', help='module:function, path.py:function, or an exact compiled JSON artifact')
        sub.add_argument('--parameters', type=Path)
        sub.add_argument('--clock-hz', type=int, default=125000000)
        sub.add_argument('--freq', type=float, default=108050)
        sub.add_argument('--n-pins', type=int, default=1)
        sub.add_argument('--window-ms', type=float)
        if command == 'preview':
            sub.add_argument('--svg', type=Path)
            sub.add_argument('--compiled', type=Path)
        else:
            sub.add_argument('--axes', type=Path, required=True)
            sub.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    bindings = {} if args.parameters is None else json.loads(args.parameters.read_text())
    options = {'clock_hz': args.clock_hz, 'n_pins': args.n_pins, 'freq': args.freq, 'window_ms': args.window_ms}
    if args.command == 'preview':
        if args.program.endswith('.json'):
            if bindings:
                raise ValueError("parameters cannot alter an exact compiled artifact")
            program = program_from_dict(json.loads(Path(args.program).read_text()))
        else:
            program = compile_program(_load_factory(args.program), parameters=bindings, **options)
        if args.svg:
            args.svg.write_text(render_svg(program, window_ms=args.window_ms))
        if args.compiled:
            args.compiled.write_text(json.dumps(program_to_dict(program), indent=2) + '\n')
        print(json.dumps(program.summary(), indent=2))
    else:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        axes = json.loads(args.axes.read_text())
        manifest = []
        for index, parameters, program in parameter_scan(_load_factory(args.program), axes, bindings, **options):
            name = 'trial-%05d.json' % index
            (args.out_dir / name).write_text(json.dumps(program_to_dict(program)) + '\n')
            manifest.append({'index': index, 'parameters': parameters, 'program': name})
        (args.out_dir / 'scan.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('%d trials compiled into %s' % (len(manifest), args.out_dir))


if __name__ == '__main__':
    main()
