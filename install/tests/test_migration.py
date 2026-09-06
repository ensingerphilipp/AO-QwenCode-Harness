import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "install/migrate_project.py"
spec = importlib.util.spec_from_file_location("migrate_project", MODULE_PATH)
migrate_project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate_project)


class MigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="migration-test-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home with spaces"
        self.home.mkdir()
        self.project = {
            "id": "demo",
            "config": {
                "trackerIntake": {"enabled": True, "assignee": "alice"},
                "customFutureKey": {"preserve": True},
                "postCreate": ["echo existing"],
                "worker": {"agent": "other", "agentConfig": {"x": 1}},
                "orchestrator": {"agent": "other", "agentConfig": {"y": 2}},
            },
        }

    def test_merge_preserves_unknown_and_project_specific_config(self):
        merged = migrate_project.merged_config(self.project, self.home)
        self.assertEqual(
            merged["trackerIntake"],
            {"enabled": True, "assignee": "alice"},
        )
        self.assertEqual(merged["customFutureKey"], {"preserve": True})
        self.assertEqual(merged["worker"]["agent"], "qwen")
        self.assertEqual(merged["worker"]["agentConfig"], {"x": 1})
        self.assertEqual(merged["orchestrator"]["agent"], "qwen")
        self.assertEqual(merged["orchestrator"]["agentConfig"], {"y": 2})
        self.assertIn("echo existing", merged["postCreate"])
        refresh = [
            item for item in merged["postCreate"]
            if "ao-refresh-orchestrator" in item
        ]
        self.assertEqual(len(refresh), 1)
        self.assertIn("'", refresh[0])

    def test_backup_rejects_unsafe_project_id(self):
        repo = Path(self.tmp.name) / "repo-unsafe-id"
        repo.mkdir()
        migrate_project.run(["git", "init", "-q", str(repo)])
        migrate_project.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        migrate_project.run(["git", "-C", str(repo), "config", "user.name", "Test"])
        (repo / "README.md").write_text("x\n")
        migrate_project.run(["git", "-C", str(repo), "add", "README.md"])
        migrate_project.run(["git", "-C", str(repo), "commit", "-qm", "init"])
        project = dict(self.project, id="../escape")
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            migrate_project.backup(project, repo, self.home)

    def test_merge_replaces_only_prior_refresh_hook(self):
        project = json.loads(json.dumps(self.project))
        project["config"]["postCreate"] = [
            "python3 /old/path/ao-refresh-orchestrator run",
            "echo keep-me",
        ]
        merged = migrate_project.merged_config(project, self.home)
        self.assertEqual(sum("ao-refresh-orchestrator" in x for x in merged["postCreate"]), 1)
        self.assertIn("echo keep-me", merged["postCreate"])

    def test_backup_contains_restorable_files_and_hashes(self):
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        migrate_project.run(["git", "init", "-q", str(repo)])
        migrate_project.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        migrate_project.run(["git", "-C", str(repo), "config", "user.name", "Test"])
        (repo / "PROJECT.md").write_text("project contract\n")
        migrate_project.run(["git", "-C", str(repo), "add", "PROJECT.md"])
        migrate_project.run(["git", "-C", str(repo), "commit", "-qm", "init"])

        host_rule = self.home / ".ao/rules/agentRules.md"
        host_rule.parent.mkdir(parents=True)
        host_rule.write_text("worker rules\n")
        snapshot = migrate_project.backup(self.project, repo, self.home)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        manifest = json.loads((snapshot / "manifest.json").read_text())

        copied = snapshot / "host/.ao/rules/agentRules.md"
        self.assertEqual(copied.read_text(), "worker rules\n")
        entry = manifest["files"]["host/.ao/rules/agentRules.md"]
        self.assertEqual(entry["sha256"], migrate_project.sha256(copied))
        self.assertEqual((snapshot / "project/PROJECT.md").read_text(), "project contract\n")

    def test_apply_requires_global_quiescence_before_mutation(self):
        from unittest import mock

        repo = Path(self.tmp.name) / "repo-quiescence"
        repo.mkdir()
        migrate_project.run(["git", "init", "-q", str(repo)])
        migrate_project.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        migrate_project.run(["git", "-C", str(repo), "config", "user.name", "Test"])
        (repo / "README.md").write_text("x\n")
        migrate_project.run(["git", "-C", str(repo), "add", "README.md"])
        migrate_project.run(["git", "-C", str(repo), "commit", "-qm", "init"])

        project = dict(self.project, path=str(repo), defaultBranch="main")
        with mock.patch.object(migrate_project, "get_project", return_value=project), \
             mock.patch.object(migrate_project, "active_sessions", return_value=[{"id": "live", "isTerminated": False}]), \
             mock.patch.object(migrate_project, "apply_globals") as apply_globals, \
             mock.patch.object(migrate_project, "apply_project_config") as apply_config, \
             mock.patch("sys.argv", ["migrate_project.py", "--project-id", "demo", "--repo", str(repo), "--home", str(self.home), "--apply-host"]):
            self.assertEqual(migrate_project.main(), 1)
            apply_globals.assert_not_called()
            apply_config.assert_not_called()


    def test_restore_reverts_existing_and_removes_new_host_files(self):
        repo = Path(self.tmp.name) / "repo-restore"
        repo.mkdir()
        migrate_project.run(["git", "init", "-q", str(repo)])
        migrate_project.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        migrate_project.run(["git", "-C", str(repo), "config", "user.name", "Test"])
        (repo / "README.md").write_text("x\n")
        migrate_project.run(["git", "-C", str(repo), "add", "README.md"])
        migrate_project.run(["git", "-C", str(repo), "commit", "-qm", "init"])

        rule = self.home / ".ao/rules/agentRules.md"
        rule.parent.mkdir(parents=True)
        rule.write_text("before\n")
        snapshot = migrate_project.backup(self.project, repo, self.home)
        rule.write_text("after\n")
        newly_created = self.home / ".qwen/QWEN.md"
        newly_created.parent.mkdir(parents=True, exist_ok=True)
        newly_created.write_text("new\n")

        original_run = migrate_project.run
        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["ao", "project", "set-config"]:
                return type("P", (), {"stdout": json.dumps({"project": self.project})})()
            return original_run(cmd, **kwargs)

        from unittest import mock
        with mock.patch.object(migrate_project, "run", side_effect=fake_run):
            migrate_project.restore_host_snapshot(snapshot, "demo")
        self.assertEqual(rule.read_text(), "before\n")
        self.assertFalse(newly_created.exists())


if __name__ == "__main__":
    unittest.main()
