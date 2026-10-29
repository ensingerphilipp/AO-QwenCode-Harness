import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onboard_project as onboarding


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.real_run = onboarding.run
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "app.txt").write_text("fixture\n")
        self.git("add", "app.txt")
        self.git("commit", "-m", "initial")
        self.real_run(["git", "init", "--bare", "-b", "main", str(self.root / "remote.git")])
        self.git("remote", "add", "origin", str(self.root / "remote.git"))
        self.git("push", "-u", "origin", "main")
        self.git("remote", "set-head", "origin", "--auto")
        self.records = {}
        self.calls = []
        self.fail_lookup = False
        self.fail_config = False
        self.patch = patch.object(onboarding, "run", side_effect=self.fake_run)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def git(self, *args, repo=None):
        return self.real_run(["git", "-C", str(repo or self.repo), *args])

    def fake_run(self, argv):
        if argv[0] == "python3" and argv[1].endswith("verify_install.py"):
            return "Host-global harness installation verified."
        if argv[:2] != ["ao", "project"]:
            return self.real_run(argv)
        self.calls.append(argv)
        action = argv[2]
        if self.fail_lookup and action in ("ls", "get"):
            raise RuntimeError("simulated unavailable AO")
        if action == "ls":
            return json.dumps({"projects": [{"id": ident} for ident in self.records]})
        if action == "get":
            return json.dumps({"project": self.records[argv[3]]})
        if action == "add":
            ident = argv[argv.index("--id") + 1]
            if ident in self.records:
                raise RuntimeError("duplicate ID")
            self.records[ident] = {"id": ident, "path": argv[argv.index("--path") + 1],
                                   "kind": "single_repo", "config": {}}
            return ""
        if action == "set-config":
            if self.fail_config:
                raise RuntimeError("simulated config failure")
            self.records[argv[3]]["config"] = json.loads(argv[argv.index("--config-json") + 1])
            return json.dumps({"project": self.records[argv[3]]})
        raise AssertionError(f"unexpected AO mutation: {action}")

    def inspection(self, repo=None):
        repo = repo or self.repo
        tokens = {token for group in onboarding.baseline.MANIFEST["renderedFiles"].values() for token in group}
        values = {token: "Fixture project fact." for token in tokens}
        values.update(PROJECT_NAME="Fixture", CI_RUNNER="ubuntu-24.04",
                      CI_TIMEOUT_MINUTES="5", CI_SETUP_STEPS="")
        doc = {"schemaVersion": 2,
               "repository": {"root": str(repo), "remote": None, "defaultBranch": "main"},
               "facts": [], "templateValues": values, "decisionsRequired": [],
               "verification": {"selectedProfiles": ["generic"], "preflight": [],
                                "checks": [{"id": "fixture", "command": "test -f app.txt",
                                            "rationale": "Verify tracked fixture",
                                            "evidence": [{"path": "app.txt", "detail": "Tracked input"}]}]}}
        path = self.root / ("inspection-" + repo.name + ".json")
        path.write_text(json.dumps(doc))
        return path

    def publish_baseline(self, project_id="new"):
        inspection = self.inspection()
        onboarding.prepare(self.repo, project_id, inspection, "enabled")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.git("push", "origin", "main")
        return inspection

    def add_existing(self, ident="gui"):
        config = {"worker": {"agent": "qwen", "agentConfig": {"model": "chosen"}},
                  "orchestrator": {"agent": "qwen", "mode": "chat"},
                  "env": {"PRIVATE_FIXTURE": "preserved"}, "symlinks": ["data"],
                  "postCreate": ["npm ci", "echo ao-refresh-orchestrator-custom"],
                  "trackerIntake": {"enabled": False}, "defaultBranch": "main"}
        self.records[ident] = {"id": ident, "path": str(self.repo), "kind": "single_repo", "config": config}
        return copy.deepcopy(config)

    def test_unregistered_repo_registers_once_and_retry_reuses(self):
        inspection = self.publish_baseline()
        first = onboarding.configure(self.repo, "new", inspection, "enabled", self.home)
        second = onboarding.configure(self.repo, "new", inspection, "enabled", self.home)
        self.assertEqual((first["registration"], second["registration"]), ("created", "reused"))
        self.assertEqual(sum(c[2] == "add" for c in self.calls), 1)
        self.assertEqual(sum(c[2] == "set-config" for c in self.calls), 1)
        self.assertEqual(self.records["new"]["path"], str(self.repo))

    def test_gui_registration_preserves_unrelated_config_and_hooks(self):
        original = self.add_existing()
        inspection = self.publish_baseline("gui")
        result = onboarding.configure(self.repo, None, inspection, "enabled", self.home)
        self.assertEqual(result["registration"], "reused")
        self.assertFalse(any(c[2] == "add" for c in self.calls))
        config = self.records["gui"]["config"]
        for key in ("env", "symlinks", "trackerIntake", "defaultBranch"):
            self.assertEqual(config[key], original[key])
        self.assertEqual(config["worker"]["agentConfig"], original["worker"]["agentConfig"])
        self.assertEqual(config["orchestrator"]["mode"], "chat")
        self.assertEqual(config["postCreate"][:-1], original["postCreate"])
        self.assertIn(shlex.quote(str(self.home / ".local/bin/ao-refresh-orchestrator")), config["postCreate"][-1])
        self.assertEqual(Path(result["configBackup"]).stat().st_mode & 0o777, 0o600)

    def test_worktree_and_alias_resolve_existing_project(self):
        self.add_existing()
        worktree = self.root / "orchestrator"
        self.git("worktree", "add", "-b", "onboarding", str(worktree), "main")
        alias = self.root / "alias"
        alias.symlink_to(worktree, target_is_directory=True)
        root, primary, project = onboarding.resolve(alias)
        self.assertEqual(root, worktree)
        self.assertEqual(primary, self.repo)
        self.assertEqual(project["id"], "gui")

    def test_unregistered_worktree_registers_primary_checkout(self):
        worktree = self.root / "onboard"
        self.git("worktree", "add", "-b", "onboarding", str(worktree), "main")
        inspection = self.inspection(worktree)
        onboarding.prepare(worktree, "new", inspection, "enabled")
        self.git("add", ".", repo=worktree)
        self.git("commit", "-m", "baseline", repo=worktree)
        with self.assertRaises(RuntimeError):
            onboarding.configure(worktree, "new", inspection, "enabled", self.home)
        self.assertFalse(any(c[2] == "add" for c in self.calls))
        self.git("merge", "--ff-only", "onboarding")
        self.git("push", "origin", "main")
        onboarding.configure(worktree, "new", inspection, "enabled", self.home)
        self.assertEqual(self.records["new"]["path"], str(self.repo))

    def test_lookup_failure_never_becomes_unregistered(self):
        self.fail_lookup = True
        with self.assertRaisesRegex(RuntimeError, "unavailable AO"):
            onboarding.resolve(self.repo, "new")
        self.assertFalse(any(c[2] in ("add", "set-config") for c in self.calls))

    def test_id_mismatch_and_ambiguous_registration_refused(self):
        self.add_existing()
        with self.assertRaisesRegex(RuntimeError, "differs"):
            onboarding.resolve(self.repo, "new")
        self.records["duplicate"] = {**self.records["gui"], "id": "duplicate"}
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            onboarding.resolve(self.repo)

    def test_existing_file_conflict_precedes_any_writes(self):
        (self.repo / "ARCHITECTURE.md").write_text("Existing owned architecture")
        self.git("add", ".")
        self.git("commit", "-m", "existing contract")
        with self.assertRaisesRegex(RuntimeError, "existing project file differs"):
            onboarding.prepare(self.repo, "new", self.inspection(), "enabled")
        self.assertFalse((self.repo / "PROJECT.md").exists())
        self.assertFalse(any(c[2] in ("add", "set-config") for c in self.calls))

    def test_symlink_target_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / ".github").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlinked"):
            onboarding.prepare(self.repo, "new", self.inspection(), "enabled")
        self.assertEqual(list(outside.iterdir()), [])

    def test_only_ao_runtime_file_exempted_from_clean_check(self):
        runtime = self.repo / ".qwen/settings.json"
        runtime.parent.mkdir()
        runtime.write_text("{}")
        onboarding.prepare(self.repo, "new", self.inspection(), "enabled")
        self.assertEqual(runtime.read_text(), "{}")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        runtime.write_text('{"changed":true}')
        with self.assertRaisesRegex(RuntimeError, "clean"):
            onboarding.require_clean(self.repo)

    def test_config_failure_retains_registration_and_retry_does_not_duplicate(self):
        inspection = self.publish_baseline()
        self.fail_config = True
        with self.assertRaisesRegex(RuntimeError, "config failure"):
            onboarding.configure(self.repo, "new", inspection, "enabled", self.home)
        self.assertIn("new", self.records)
        self.fail_config = False
        result = onboarding.configure(self.repo, "new", inspection, "enabled", self.home)
        self.assertEqual(result["registration"], "reused")
        self.assertEqual(sum(c[2] == "add" for c in self.calls), 1)
        self.assertEqual(self.records["new"]["config"]["defaultBranch"], "main")

    def test_existing_match_survives_unrelated_stale_registration(self):
        self.add_existing()
        self.records["stale"] = {"id": "stale", "path": str(self.root / "missing"), "config": {}}
        self.assertEqual(onboarding.resolve(self.repo)[2]["id"], "gui")
        del self.records["gui"]
        with self.assertRaisesRegex(RuntimeError, "cannot prove"):
            onboarding.resolve(self.repo, "new")
        self.assertFalse(any(c[2] == "add" for c in self.calls))

    def test_activation_requires_remote_and_accepts_sole_nonorigin_remote(self):
        inspection = self.publish_baseline()
        self.git("remote", "rename", "origin", "upstream")
        onboarding.configure(self.repo, "new", inspection, "enabled", self.home)
        self.git("remote", "remove", "upstream")
        with self.assertRaisesRegex(RuntimeError, "unambiguous Git remote"):
            onboarding.configure(self.repo, "new", inspection, "enabled", self.home)

    def test_user_only_skill_and_wrapper_source_lookup(self):
        skill = onboarding.ROOT / "global/qwen/skills/ao-init-project"
        text = (skill / "SKILL.md").read_text()
        self.assertIn("user-invocable: true", text)
        self.assertIn("disable-model-invocation: true", text)
        state = self.root / "state"
        manifest = state / "ao-qwen-code-harness/install-manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"sourceRepository": str(onboarding.ROOT)}))
        import os
        result = subprocess.run([sys.executable, str(skill / "scripts/onboard.py"), "--help"],
                                env={**os.environ, "XDG_STATE_HOME": str(state)},
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("inspect,prepare,configure", result.stdout)


if __name__ == "__main__":
    unittest.main()

