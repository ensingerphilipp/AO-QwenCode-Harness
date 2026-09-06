import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "install/init_project.py"
spec = importlib.util.spec_from_file_location("init_project", MODULE_PATH)
init_project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_project)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ao-register-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.home = Path(self.tmp.name) / "home with spaces"
        self.home.mkdir()
        self.doc = {"repository": {"defaultBranch": "main"}}

    def result(self):
        return subprocess.CompletedProcess([], 0, "", "")

    def test_registration_uses_supported_cli_surface(self):
        calls = []

        def fake_run(cmd, check=True):
            calls.append(cmd)
            return self.result()

        with mock.patch.object(init_project, "run", side_effect=fake_run):
            init_project.register_ao(
                self.repo, "demo", "Demo", self.doc, self.home, "alice"
            )

        self.assertEqual(calls[0][:3], ["ao", "project", "add"])
        self.assertIn("--worker-agent", calls[0])
        self.assertIn("--orchestrator-agent", calls[0])
        self.assertEqual(calls[1][:3], ["ao", "project", "set-config"])
        self.assertIn("--agent-rules", calls[1])
        self.assertIn("--orchestrator-rules", calls[1])
        self.assertIn("--post-create", calls[1])
        self.assertIn("--tracker-intake", calls[1])
        post = calls[1][calls[1].index("--post-create") + 1]
        self.assertIn("'", post)

    def test_failed_set_config_rolls_back_new_registration(self):
        calls = []

        def fake_run(cmd, check=True):
            calls.append(cmd)
            if cmd[:3] == ["ao", "project", "set-config"]:
                raise RuntimeError("set-config failed")
            return self.result()

        with mock.patch.object(init_project, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "set-config failed"):
                init_project.register_ao(
                    self.repo, "demo", "Demo", self.doc, self.home, None
                )

        self.assertEqual(calls[-1], ["ao", "project", "rm", "demo"])


if __name__ == "__main__":
    unittest.main()
