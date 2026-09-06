import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "install/verify_install.py"
spec = importlib.util.spec_from_file_location("verify_install", MODULE_PATH)
verify_install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_install)


class ProjectConfigInvariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="verify-project-config-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home with spaces"
        self.home.mkdir()

    def valid_config(self):
        return {
            "agentRules": f"Before any task action, read and follow the file at `{self.home / '.ao/rules/agentRules.md'}`.",
            "orchestratorRules": f"Before any coordination action, read and follow the file at `{self.home / '.ao/rules/orchestratorRules.md'}`.",
            "worker": {"agent": "qwen"},
            "orchestrator": {"agent": "qwen"},
            "postCreate": [f"python3 {self.home / '.local/bin/ao-refresh-orchestrator'} run"],
        }

    def test_exact_harness_config_passes(self):
        self.assertEqual(verify_install.project_config_failures(self.valid_config(), self.home), [])

    def test_drifted_loader_and_agent_fail(self):
        config = self.valid_config()
        config["orchestratorRules"] = "inline giant rules"
        config["worker"]["agent"] = "other"
        failures = verify_install.project_config_failures(config, self.home)
        self.assertIn("AO project orchestratorRules loader does not match harness invariant", failures)
        self.assertIn("AO project worker.agent is not qwen", failures)


if __name__ == "__main__":
    unittest.main()
