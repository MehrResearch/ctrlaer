#!/usr/bin/env python3
"""Generate the focused API pages from signatures and docstrings.

Run ``uv run tools/build_api.py`` after editing a public docstring.
Use ``--check`` to check the generated pages without writing them.
"""
import argparse
import ast
from pathlib import Path
import re
import textwrap

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    ('controller', 'Controllers', 'Connect a program to its output pins and run it.',
     [('ctrlaer', 'CtrlAer')]),
    ('signals', 'Signals and timing', 'Use these commands inside your experiment function.',
     [('ctrlaer_commands', name) for name in ('square_wave', 'sweep', 'sleep', 'print')] + [('ctrlaer', 'mux')]),
    ('triggers', 'Triggers and groups', 'Wait for an external input, or start independent controllers together.',
     [('ctrlaer_program', 'Trigger'), ('ctrlaer_commands', 'wait_for'), ('ctrlaer', 'Group')]),
    ('runs', 'Run objects', 'A controller returns these objects from prepare(). Use them to start, wait for, stop and repeat the experiment.',
     [('ctrlaer_runtime', 'PreparedRun'), ('ctrlaer_buffered', 'BufferedRun')]),
    ('analysis', 'Previews and compiled programs', 'Calculate an output sequence, inspect it and exchange it with another program.',
     [('ctrlaer_program', 'compile_program'), ('ctrlaer_program', 'CompiledProgram')] +
     [('ctrlaer_tools', name) for name in ('render_svg', 'parameter_scan', 'program_to_dict', 'program_from_dict')]),
    ('capture', 'Digital measurements', 'Record HIGH and LOW levels and compare them with the expected output.',
     [('ctrlaer_capture', 'DigitalCapture'), ('ctrlaer_capture', 'Trace')]),
    ('records', 'Experiment records', 'Save the signal, measurements, settings and results together.',
     [('ctrlaer_records', name) for name in ('save_record', 'load_record', 'fingerprint', 'device_info')]),
    ('errors', 'Errors', 'These errors stop a prepared or buffered experiment. Inspect the exception and run.status() to find the cause.',
     [('ctrlaer_runtime', name) for name in ('UnderrunError', 'ExecutionTimeoutError', 'GateTimeoutError')] + [('ctrlaer_capture', 'CaptureOverrunError')]),
]

CONSTANTS = '''## Fixed output commands

Use these constants with a duration, for example `yield HIGH, 20`.

| Command | Output on one channel |
|---|---|
| `HIGH`, `PULSE11` | Constant HIGH |
| `LOW`, `OFF`, `PULSE00` | Constant LOW |
| `ON`, `PULSE10` | A square wave: LOW for the first half of each cycle, HIGH for the second |
| `PULSE01` | A square wave: HIGH for the first half of each cycle, LOW for the second |

`ON` and `PULSE01` use the controller's `freq`. `square_wave()` and `sweep()`
specify their own frequency settings. Durations are milliseconds unless you
choose `use_ms=False`, which counts periods of the controller's `freq`.

`FREQ` is the default frequency, 108050 Hz. `RP_CLK` is the board's clock
frequency when CtrlAer was imported, or `None` on desktop Python.
'''

def signature(node, name):
    arguments = ast.unparse(node.args)
    if arguments in ('self', 'cls'):
        arguments = ''
    elif arguments.startswith(('self, ', 'cls, ')):
        arguments = arguments.split(', ', 1)[1]
    label = f'{name}({arguments})'
    if len(label) > 88:
        # AST gives stable representations; break at argument boundaries only.
        chunks, start, nesting, quote, escaped = [], 0, 0, None, False
        for index, char in enumerate(arguments):
            if quote:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char in '([{':
                nesting += 1
            elif char in ')]}':
                nesting -= 1
            elif char == ',' and not nesting:
                chunks.append(arguments[start:index].strip())
                start = index + 1
        chunks.append(arguments[start:].strip())
        label = name + '(\n' + '\n'.join('    ' + chunk + ',' for chunk in chunks) + '\n)'
    return '```python\n' + label + '\n```'

def render_doc(doc):
    if not doc:
        raise ValueError('A public object is missing its docstring')
    parts = re.split(r'(?m)^(Args|Returns|Raises|Examples|Notes):\n', doc)
    output = [parts[0].strip()]
    for index in range(1, len(parts), 2):
        title, content = parts[index], textwrap.dedent(parts[index+1]).strip()
        if title == 'Args':
            rows = []
            trailing = []
            for line in content.splitlines():
                if trailing:
                    trailing.append(line)
                    continue
                match = re.match(r'^ {0,4}([*\w]+)(?: \(([^)]+)\))?:\s*(.*)', line)
                if match:
                    rows.append([match[1], match[2] or '', match[3]])
                elif line and not line.startswith(' '):
                    trailing.append(line)
                elif rows:
                    rows[-1][2] += ' ' + line.strip()
            table = '| Argument | Type | Meaning |\n|---|---|---|\n'
            table += '\n'.join('| `{}` | {} | {} |'.format(name, kind, description.replace('|', '\\|'))
                               for name, kind, description in rows)
            output.append(table)
            if trailing:
                output.append('\n'.join(trailing).strip())
        elif title == 'Notes':
            output.append(content)
        else:
            output.append('**' + title + '**\n\n' + content)
    return '\n\n'.join(output)

def build():
    modules = {}
    for module in {module for *_, objects in PAGES for module, _ in objects} | {'ctrlaer_runtime'}:
        tree = ast.parse((ROOT/(module+'.py')).read_text())
        modules[module] = {node.name: node for node in tree.body
                           if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    generated = {}
    hidden_constructors = {'PreparedRun', 'BufferedRun', 'CompiledProgram', 'Trace'}

    def document(node, name, depth=2):
        lines = ['#'*depth + ' ' + name]
        if isinstance(node, ast.FunctionDef):
            is_property = any(isinstance(d, ast.Name) and d.id == 'property' for d in node.decorator_list)
            lines.append('```python\n' + name + '\n```' if is_property else signature(node, name))
        else:
            constructors = [child for child in node.body if isinstance(child, ast.FunctionDef)
                            and child.name in ('__init__', '__new__')]
            if constructors and node.name not in hidden_constructors:
                lines.append(signature(constructors[0], name))
        lines.append(render_doc(ast.get_docstring(node)))
        if isinstance(node, ast.ClassDef):
            source = modules['ctrlaer_runtime']['PreparedGroup'] if name == 'Group' else node
            for child in source.body:
                if not isinstance(child, ast.FunctionDef) or child.name.startswith('_'):
                    continue
                if any(isinstance(d, ast.Attribute) and d.attr == 'setter' for d in child.decorator_list):
                    continue
                lines.append(document(child, name+'.'+child.name, depth+1))
            if name == 'BufferedRun':
                lines.append('Other methods and properties are inherited from `PreparedRun`, described above.')
        return '\n\n'.join(lines)

    links = []
    for slug, title, intro, objects in PAGES:
        content = ['# '+title, '<!-- Generated by tools/build_api.py; edit the source docstrings. -->', intro]
        if slug == 'signals':
            content.append(CONSTANTS.strip())
        content += [document(modules[module][name], name) for module, name in objects]
        if slug == 'signals':
            content += ['## Predefined generators']
            for name in ('always_on', 'inactive'):
                content += ['### '+name, render_doc(ast.get_docstring(modules['ctrlaer']['_'+name]))]
        generated['docs/api/'+slug+'.md'] = '\n\n'.join(content)+'\n'
        labels = ', '.join('`'+name+'`' for _, name in objects)
        links.append('| ['+title+'](api/'+slug+'.md) | '+labels+' |')
    generated['docs/api-reference.md'] = '''# API reference

Use the reference to look up arguments, defaults and return values. The
[getting-started guide](getting-started.md) explains the program structure;
[worked examples](examples.md) show complete experiments and connections.

| Reference | Objects |
|---|---|
'''+'\n'.join(links)+'''

The controller, input waits and digital capture run on MicroPython. Previews,
parameter scans and record loading also work on your computer. The signatures
and descriptions on these pages come from the library's source docstrings.
'''
    return generated

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    generated = build()
    stale = []
    for filename, content in generated.items():
        path = ROOT/filename
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(filename)
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
    if stale:
        parser.exit(1, 'API pages are stale; run uv run tools/build_api.py\n'+'\n'.join(stale)+'\n')
    print('Checked' if args.check else 'Updated', len(generated), 'API pages.')

if __name__ == '__main__':
    main()
