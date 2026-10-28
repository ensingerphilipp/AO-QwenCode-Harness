import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('replace', Path(__file__).parents[1] / 'scripts/replace.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

class ReplacementTests(unittest.TestCase):
    def test_hidden_from_model(self):
        skill = (Path(__file__).parents[1] / 'SKILL.md').read_text()
        self.assertIn('user-invocable: true', skill)
        self.assertIn('disable-model-invocation: true', skill)

    def test_strict_args(self):
        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write('project; ao session kill other')
            f.flush()
            with self.assertRaises(ValueError): MOD.load_args(f.name)

    def test_fails_before_mutation_when_old_session_not_exited(self):
        with patch.object(MOD, 'gate_fixed_daemon', return_value='a'*40), patch.object(MOD, 'run', return_value='{"project":{"id":"project"}}') as run, patch.object(MOD, 'sessions', return_value={'x': {'id':'x','status':'idle'}}):
            with self.assertRaisesRegex(RuntimeError, 'exactly one exited'): MOD.replace('project')
            self.assertEqual(run.call_count, 1)  # read project only

    def test_terminates_exited_session_then_spawns_once(self):
        states=[{'old':{'id':'old','status':'exited'}}, {'old':{'id':'old','status':'terminated'}}, {'old':{'id':'old','status':'terminated'},'new':{'id':'new','status':'idle'}}]
        with patch.object(MOD, 'gate_fixed_daemon', return_value='a'*40), patch.object(MOD, 'run', return_value='{"project":{"id":"project"}}') as run, patch.object(MOD, 'sessions', side_effect=states), patch.object(MOD, 'inspect_binding', return_value=True):
            MOD.replace('project')
            commands=[c.args for c in run.call_args_list]
            self.assertEqual(len([c for c in commands if c[:3]==('ao','session','kill')]),1)
            self.assertEqual(len([c for c in commands if c[:2]==('ao','spawn')]),1)
            self.assertLess(next(i for i,c in enumerate(commands) if c[:3]==('ao','session','kill')), next(i for i,c in enumerate(commands) if c[:2]==('ao','spawn')))

if __name__=='__main__': unittest.main()
