import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install/install_host.py"
VERIFY = ROOT / "install/verify_install.py"
INIT = ROOT / "install/init_project.py"
TOKENS = sorted({
    token
    for tokens in json.loads(
        (ROOT / "templates/project/template-manifest.json").read_text()
    )["renderedFiles"].values()
    for token in tokens
})


class InstallHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="harness-install-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.root / "state")}

    def run_installer(self, *args, check=True):
        proc = subprocess.run(
            ["python3", str(INSTALL), "--home", str(self.home),
             "--skip-command-check", *args],
            env=self.env, text=True, capture_output=True,
        )
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_install_is_idempotent_and_verifiable(self):
        self.run_installer()
        self.run_installer()
        proc = subprocess.run(
            ["python3", str(VERIFY), "--home", str(self.home),
             "--skip-runtime-checks"],
            text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads(
            (self.root / "state/ao-qwen-code-harness/install-manifest.json")
            .read_text()
        )
        self.assertIn(".qwen/QWEN.md", manifest["files"])
        self.assertIn(".qwen/skills/ao-pr-review/SKILL.md", manifest["files"])
        self.assertIn(
            ".qwen/skills/ao-semantic-review-override/SKILL.md",
            manifest["files"],
        )

    def test_unmanaged_collision_refused_and_replace_backed_up(self):
        target = self.home / ".qwen/QWEN.md"
        target.parent.mkdir(parents=True)
        target.write_text("local policy\n")
        refused = self.run_installer(check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(target.read_text(), "local policy\n")
        self.run_installer("--replace")
        backups = list(
            (self.root / "state/ao-qwen-code-harness/backups").rglob("QWEN.md")
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "local policy\n")


class InitProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="harness-project-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email",
             "test@example.com"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )
        (self.repo / "README.md").write_text("# demo\n")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "README.md"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "initial"],
            check=True, capture_output=True,
        )
        self.inspection = self.root / "inspection.json"
        values = {token: f"value for {token}" for token in TOKENS}
        values.update({
            "PROJECT_NAME": "Demo",
            "CI_RUNNER": "ubuntu-24.04",
            "CI_TIMEOUT_MINUTES": "10",
            "CI_SETUP_STEPS": "      - name: Setup\n        run: true",
        })
        doc = {
            "schemaVersion": 1,
            "repository": {
                "root": str(self.repo),
                "remote": "https://github.com/acme/demo.git",
                "defaultBranch": "main",
            },
            "facts": [],
            "templateValues": values,
            "verification": {
                "selectedProfiles": ["generic"],
                "preflight": [{
                    "id": "preflight", "command": "true",
                    "rationale": "test",
                    "evidence": [{"path": "README.md", "detail": "test"}],
                }],
                "checks": [{
                    "id": "check", "command": "true",
                    "rationale": "test",
                    "evidence": [{"path": "README.md", "detail": "test"}],
                }],
            },
            "reviewRisk": {
                "highRiskPaths": ["src/security/**"],
                "highRiskLabels": ["security"], "evidence": [],
            },
            "decisionsRequired": [],
        }
        self.inspection.write_text(json.dumps(doc))

    def run_init(self, *extra):
        return subprocess.run(
            ["python3", str(INIT), "--repo", str(self.repo),
             "--inspection", str(self.inspection), "--project-id", "demo",
             "--render-only", *extra],
            text=True, capture_output=True,
        )

    def test_rendered_project_is_complete_and_verify_passes(self):
        proc = self.run_init()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = (
            "PROJECT.md", "ARCHITECTURE.md", "QWEN.md",
            ".qwen/review-rules.md", ".qwen/review-config.json",
            ".agent-harness.json", ".github/workflows/verify.yml",
            "scripts/verify",
        )
        for rel in files:
            self.assertNotIn("{{", (self.repo / rel).read_text())
        self.assertTrue(os.access(self.repo / "scripts/verify", os.X_OK))
        verify = subprocess.run(
            ["bash", "scripts/verify"], cwd=self.repo,
            text=True, capture_output=True,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        risk = json.loads(
            (self.repo / ".qwen/review-config.json").read_text()
        )
        self.assertEqual(risk["highRiskPaths"], ["src/security/**"])

    def test_unresolved_decision_blocks_without_writes(self):
        doc = json.loads(self.inspection.read_text())
        doc["decisionsRequired"] = [{
            "id": "d1", "question": "q", "reason": "r",
            "affectedTokens": ["PROJECT_GOALS"],
        }]
        doc["templateValues"]["PROJECT_GOALS"] = None
        self.inspection.write_text(json.dumps(doc))
        proc = self.run_init()
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.repo / "PROJECT.md").exists())

    def test_existing_conflicting_contract_is_refused(self):
        (self.repo / "PROJECT.md").write_text("existing\n")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "PROJECT.md"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "contract"],
            check=True, capture_output=True,
        )
        proc = self.run_init()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual((self.repo / "PROJECT.md").read_text(), "existing\n")


if __name__ == "__main__":
    unittest.main()
