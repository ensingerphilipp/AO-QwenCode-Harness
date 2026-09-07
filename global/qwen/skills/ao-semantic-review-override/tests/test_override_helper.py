import base64
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/override_status.py"
spec = importlib.util.spec_from_file_location("override_status", HELPER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OverrideHelperTests(unittest.TestCase):
    def test_skill_is_manual_only(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("user-invocable: true", text)
        self.assertIn("disable-model-invocation: true", text)

    def test_parse_requires_reason(self):
        with self.assertRaises(ValueError):
            mod.parse_invocation("84")
        self.assertEqual(mod.parse_invocation('84 "migration exception"'),
                         ("84", "migration exception"))

    def test_description_is_bounded(self):
        text = mod.desired_description("a" * 40, "x" * 300)
        self.assertLessEqual(len(text), 140)
        self.assertTrue(text.startswith("Manual operator override for "))

    def test_lifecycle_requires_explicit_true(self):
        payload = base64.b64encode(b'{"semanticReview":{"enabled":true}}').decode()
        with mock.patch.object(mod, "gh_json", return_value={"content": payload}):
            mod.lifecycle_enabled("o/r", "a" * 40)
        bad = base64.b64encode(b'{"semanticReview":{"enabled":false}}').decode()
        with mock.patch.object(mod, "gh_json", return_value={"content": bad}):
            with self.assertRaises(RuntimeError):
                mod.lifecycle_enabled("o/r", "a" * 40)

    def test_execute_refuses_moved_head(self):
        first = {"number": 84, "url": "https://github.com/o/r/pull/84",
                 "headRefOid": "a" * 40}
        second = dict(first, headRefOid="b" * 40)
        with mock.patch.object(mod, "repo_name", return_value="o/r"), \
             mock.patch.object(mod, "pr_info", side_effect=[first, second]), \
             mock.patch.object(mod, "lifecycle_enabled"), \
             mock.patch.object(mod, "deterministic_checks_pass"), \
             mock.patch.object(mod, "publish") as publish:
            with self.assertRaises(RuntimeError):
                mod.execute("84", "reason")
            publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
