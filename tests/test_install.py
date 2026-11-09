"""Exercise device selection and real filesystem upgrade/rollback transactions."""
import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3,11):
            raise unittest.SkipTest('PEP 723 installer requires Python 3.11+')
        spec=importlib.util.spec_from_file_location('ctrlaer_installer',Path(__file__).parents[1]/'tools/install.py')
        cls.installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.installer)

    def test_selection_prompts_instead_of_silently_choosing_a_board(self):
        ports=[SimpleNamespace(device='/dev/one',serial_number='tiny',description='Board'),
               SimpleNamespace(device='/dev/two',serial_number='pico',description='Board')]
        answers=iter(['invalid','2'])
        with contextlib.redirect_stdout(io.StringIO()):
            chosen=self.installer.select_device(None,ports,input_fn=lambda prompt:next(answers))
        self.assertEqual(chosen,'/dev/two')
        self.assertEqual(self.installer.select_device('tiny',ports),'/dev/one')
        self.assertEqual(self.installer.select_device('/dev/explicit',ports),'/dev/explicit')

    def test_checkout_snapshot_keeps_compilation_and_manifest_sources_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout=Path(directory)/'checkout';checkout.mkdir()
            temporary=Path(directory)/'install';temporary.mkdir()
            (checkout/'pyproject.toml').write_text(
                '[project]\nversion="test"\n[tool.setuptools]\npy-modules=["ctrlaer_test"]\n')
            original=checkout/'ctrlaer_test.py';original.write_bytes(b'value = 1\n')
            root,modules,version,provenance=self.installer.source_tree(checkout,None,temporary)
            original.write_bytes(b'value = 2\n')
            self.assertEqual((root/'ctrlaer_test.py').read_bytes(),b'value = 1\n')
            self.assertEqual(modules,['ctrlaer_test'])
            self.assertEqual(version,'test')
            self.assertEqual(provenance['kind'],'checkout')

    def activation(self,root,manifest,broken_import=False):
        script=(self.installer.ACTIVATE%json.dumps(manifest)).replace("'/lib",repr(str(root))[:-1])
        original_import=builtins.__import__
        def imported(name,*args,**kwargs):
            if name=='ctrlaer_test':
                if broken_import:raise ImportError('incompatible native module')
                return SimpleNamespace(__file__=str(root/(name+'.mpy')))
            return original_import(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=imported),patch('os.sync',create=True),contextlib.redirect_stdout(io.StringIO()):
            exec(script,{'__builtins__':builtins.__dict__})

    def fixture(self,root):
        stage=root/'.ctrlaer-stage';stage.mkdir()
        (root/'ctrlaer_test.py').write_bytes(b'old source')
        (root/'main.py').write_bytes(b'user application')
        (root/'ctrlaer-installed.json').write_text('{"old":true}')
        (stage/'ctrlaer_test.mpy').write_bytes(b'new compiled code')
        return {'version':'test','modules':['ctrlaer_test'],
                'files':{'ctrlaer_test.mpy':hashlib.sha256(b'new compiled code').hexdigest()}}

    def test_verified_upgrade_preserves_app_and_previous_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest=self.fixture(root)
            self.activation(root,manifest)
            self.assertEqual((root/'ctrlaer_test.mpy').read_bytes(),b'new compiled code')
            self.assertFalse((root/'ctrlaer_test.py').exists())
            self.assertEqual((root/'.ctrlaer-previous/ctrlaer_test.py').read_bytes(),b'old source')
            self.assertEqual((root/'main.py').read_bytes(),b'user application')
            self.assertEqual(json.loads((root/'ctrlaer-installed.json').read_text()),manifest)

    def test_corrupt_upload_does_not_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest=self.fixture(root)
            (root/'.ctrlaer-stage/ctrlaer_test.mpy').write_bytes(b'damaged upload')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):self.activation(root,manifest)
            self.assertEqual((root/'ctrlaer_test.py').read_bytes(),b'old source')
            self.assertFalse((root/'ctrlaer_test.mpy').exists())

    def test_import_failure_rolls_back_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest=self.fixture(root)
            with self.assertRaisesRegex(ImportError,'incompatible'):self.activation(root,manifest,broken_import=True)
            self.assertEqual((root/'ctrlaer_test.py').read_bytes(),b'old source')
            self.assertFalse((root/'ctrlaer_test.mpy').exists())
            self.assertEqual((root/'main.py').read_bytes(),b'user application')
            self.assertEqual(json.loads((root/'ctrlaer-installed.json').read_text()),{'old':True})


if __name__=='__main__':unittest.main()
