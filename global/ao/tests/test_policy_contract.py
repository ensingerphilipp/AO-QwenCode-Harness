from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
ORCH = (ROOT / "global/ao/rules/orchestratorRules.md").read_text()
PUB = (ROOT / "global/ao/policies/semanticReviewPublication.md").read_text()


class AOPolicyContractTests(unittest.TestCase):
    def test_semantic_toggle_is_explicit_and_fail_closed(self):
        for phrase in (
            ".agent-harness.json",
            "enabled by default",
            "exactly `false` disables",
            "malformed/unreadable config",
            "never an implicit disable",
        ):
            self.assertIn(phrase, ORCH)

    def test_disabled_review_never_publishes(self):
        self.assertIn("semantic review is disabled", ORCH)
        self.assertIn("If semantic review is disabled", PUB)
        self.assertIn("create or update neither publication", PUB)


if __name__ == "__main__":
    unittest.main()
