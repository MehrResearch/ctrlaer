import copy, contextlib, io, json
from pathlib import Path
import tempfile
import unittest
from ctrlaer import pulse, mux, compile_program
from ctrlaer_tools import program_to_dict, program_from_dict, parameter_scan, render_svg, main
from ctrlaer_records import save_record, load_record, fingerprint
from test_capture import sampled


def experiment(ms=1,carrier=10000):
    yield from mux([[(pulse(carrier),ms)],[(pulse(20000),ms)]])


class ToolsTests(unittest.TestCase):
    def program(self):
        return compile_program(experiment,n_pins=2,names=['atomiser <A>','B'])

    def test_exact_program_round_trip(self):
        p=self.program()
        restored=program_from_dict(json.loads(json.dumps(program_to_dict(p))))
        self.assertEqual(restored.words,p.words)
        self.assertEqual(restored.summary(),p.summary())

    def test_mutated_executable_words_and_metadata_are_rejected(self):
        original=program_to_dict(self.program())
        for change in ('instruction','terminal','duration','pulses'):
            value=copy.deepcopy(original)
            if change=='instruction':value['words'][0]=0xe001
            elif change=='terminal':value['words'][-2]=0
            elif change=='duration':value['duration_ticks']+=1
            else:value['pulse_counts'][0]+=1
            with self.subTest(change=change),self.assertRaises(ValueError):program_from_dict(value)

    def test_scans_use_normal_arguments_and_stable_enumeration(self):
        axes={'ms':[1,2],'carrier':[10000,20000]}
        a=[(i,params,fingerprint(p.words)) for i,params,p in parameter_scan(experiment,axes,n_pins=2)]
        b=[(i,params,fingerprint(p.words)) for i,params,p in parameter_scan(experiment,axes,n_pins=2)]
        self.assertEqual(a,b)
        self.assertEqual([v[1] for v in a],[{'carrier':10000,'ms':1},{'carrier':10000,'ms':2},
                                         {'carrier':20000,'ms':1},{'carrier':20000,'ms':2}])
        with self.assertRaises(TypeError):compile_program(experiment,parameters={'typo':1})
        with self.assertRaisesRegex(ValueError,'max_trials'):list(parameter_scan(experiment,axes,max_trials=3))

    def test_svg_escapes_names_and_bounds_complexity(self):
        p=self.program(); svg=render_svg(p)
        self.assertIn('atomiser &lt;A&gt;',svg)
        self.assertIn('10 pulses',svg)
        with self.assertRaisesRegex(ValueError,'shorter window'):render_svg(p,max_edges=2)

    def test_record_round_trip_and_corruption_detection(self):
        p=self.program(); trace=sampled(p)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'run.json'
            save_record(path,p,trace=trace,results={'sensor':123},metadata={'replicate':2})
            record=load_record(path)
            self.assertEqual(record['program'].words,p.words)
            self.assertEqual(record['capture'].words,trace.words)
            self.assertEqual(record['results'],{'sensor':123})
            self.assertEqual(record['device']['execution'],'offline')
            corrupt=json.loads(path.read_text());corrupt['capture_buffer']['sha256']='0'*64
            path.write_text(json.dumps(corrupt))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):load_record(path)

    def test_cli_loads_python_generator_factories(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            directory=Path(directory);source=directory/'experiment.py'
            source.write_text('from ctrlaer import pulse\ndef experiment(carrier=10000):\n    yield pulse(carrier), 1\n')
            svg,compiled=directory/'preview.svg',directory/'compiled.json'
            main(['preview',str(source)+':experiment','--svg',str(svg),'--compiled',str(compiled)])
            self.assertTrue(svg.read_text().startswith('<svg'))
            program_from_dict(json.loads(compiled.read_text()))
            axes=directory/'axes.json';axes.write_text(json.dumps({'carrier':[10000,20000]}))
            output=directory/'scan'
            main(['scan',str(source)+':experiment','--axes',str(axes),'--out-dir',str(output)])
            trials=json.loads((output/'scan.json').read_text())
            self.assertEqual(len(trials),2)
            for trial in trials:program_from_dict(json.loads((output/trial['program']).read_text()))


if __name__=='__main__':unittest.main()
