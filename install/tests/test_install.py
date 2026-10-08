import importlib.util
import fcntl
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


INSTALL_SPEC = importlib.util.spec_from_file_location("install_host", INSTALL)
install_host = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(install_host)


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

    def test_review_preflight_probe_accepts_nonposting_high(self):
        install_host.validate_review_preflight_probe(json.dumps({
            "target": {"type": "pr-url", "url": install_host.PROBE_REVIEW_URL},
            "effort": "high",
            "comment": {"requested": False, "effective": False},
            "extraTokens": [], "unknownFlags": [],
        }))

    def test_review_preflight_probe_rejects_standing_comment(self):
        with self.assertRaisesRegex(RuntimeError, "review.comment must be disabled"):
            install_host.validate_review_preflight_probe(json.dumps({
                "target": {"type": "pr-url", "url": install_host.PROBE_REVIEW_URL},
                "effort": "high",
                "comment": {"requested": False, "effective": True},
                "extraTokens": [], "unknownFlags": [],
            }))

    def test_review_preflight_probe_rejects_malformed_contract(self):
        for raw in ("not-json", json.dumps({"target": {}, "effort": "high", "comment": {"requested": False, "effective": False}, "extraTokens": [], "unknownFlags": []})):
            with self.assertRaises(RuntimeError):
                install_host.validate_review_preflight_probe(raw)

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
        self.assertIn(".local/bin/ao-review-queue", manifest["files"])
        self.assertNotIn(".qwen/skills/ao-pr-review/SKILL.md", manifest["files"])
        self.assertNotIn(
            ".qwen/skills/ao-pr-review/scripts/run_explicit_review.py",
            manifest["files"],
        )
        self.assertNotIn(
            ".qwen/skills/ao-pr-review/scripts/select_review_effort.py",
            manifest["files"],
        )
        self.assertIn(
            ".qwen/skills/ao-semantic-review-override/SKILL.md",
            manifest["files"],
        )

    def _add_legacy_runner_to_manifest(self, content="legacy runner\n"):
        runner = self.home / ".qwen/skills/ao-pr-review/scripts/run_explicit_review.py"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text(content)
        manifest_path = self.root / "state/ao-qwen-code-harness/install-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][".qwen/skills/ao-pr-review/scripts/run_explicit_review.py"] = {
            "sha256": install_host.sha256(runner), "mode": "0o755"
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return runner

    def test_upgrade_removes_unchanged_retired_runner(self):
        self.run_installer()
        runner = self._add_legacy_runner_to_manifest()
        self.run_installer()
        self.assertFalse(runner.exists())

    def test_upgrade_refuses_modified_retired_runner_without_replace(self):
        self.run_installer()
        runner = self._add_legacy_runner_to_manifest()
        runner.write_text("locally modified legacy runner\n")
        proc = self.run_installer(check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("locally modified retired managed target", proc.stderr)
        self.assertTrue(runner.exists())
        self.run_installer("--replace")
        self.assertFalse(runner.exists())

    def test_queue_introduction_refuses_only_held_review_lock(self):
        prior = {"schemaVersion": 1, "files": {".qwen/QWEN.md": {}}}
        lock = self.home / ".local/state/ao-pr-review/locks/o__r/pr-1.lock"
        lock.parent.mkdir(parents=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "legacy semantic review is active"):
                install_host.require_queue_upgrade_safety(prior, self.home)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        install_host.require_queue_upgrade_safety(prior, self.home)

    def test_queue_aware_install_needs_no_transition_check(self):
        prior = {"schemaVersion": 1, "files": {str(install_host.QUEUE_TARGET): {}}}
        install_host.require_queue_upgrade_safety(prior, self.home)

    def test_verify_install_requires_lifecycle_executables(self):
        self.run_installer()
        queue = self.home / ".local/bin/ao-review-queue"
        queue.chmod(0o600)
        proc = subprocess.run(
            ["python3", str(VERIFY), "--home", str(self.home), "--skip-runtime-checks"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not executable", proc.stderr)

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
            "schemaVersion": 2,
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
            ".qwen/review-rules.md",
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
