from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
ORCH = (ROOT / "global/ao/rules/orchestratorRules.md").read_text()
PUB = (ROOT / "global/ao/policies/semanticReviewPublication.md").read_text()
AGENT = (ROOT / "global/ao/rules/agentRules.md").read_text()


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

    def test_review_admission_is_host_global_deterministic_and_context_idle(self):
        for phrase in (
            "Acquire host-global review admission",
            "strict host-wide FIFO",
            "Do not create a reviewer Task",
            "Queued state is inert deterministic machine state",
            "REVIEW_SLOT_GRANTED",
            "There is no lease TTL",
        ):
            self.assertIn(phrase, ORCH)

    def test_timeout_review_resume_is_bounded_and_same_reviewer(self):
        for phrase in (
            "`timedOut: true` is the sole automatic-resume case",
            "same reviewer Task",
            "Never spawn a replacement reviewer",
            "at most one automatic resume",
            "stop for human attention",
        ):
            self.assertIn(phrase, ORCH)

    def test_semantic_summary_findings_are_actionable_not_index_only(self):
        for phrase in (
            "trusted `summary` field (not `shortSummary`)",
            "independently understandable and actionable",
            "`failureScenario`",
            "`witness`/evidence description",
            "`suggestedFix`",
            "Never omit a finding",
            "`PUBLICATION_ERROR`",
        ):
            self.assertIn(phrase, PUB)

    def test_worker_completion_handoff_is_explicit_and_regression_protected(self):
        for phrase in (
            "ao report --done --note",
            "ao report --checkpoint --note",
            "TASK_COMPLETE",
            "READY_FOR_REVIEW",
            "READY_FOR_REREVIEW",
            "SEMANTIC_REVIEW_RESULT",
            "SEMANTIC_REVIEW_FAILURE",
            "mandatory task-lifecycle event",
            "do not fall back to pane text or `ao send`",
        ):
            self.assertIn(phrase, AGENT)
        self.assertNotIn("ao send --session <ACTIVE_ORCHESTRATOR_ID>", AGENT)


if __name__ == "__main__":
    unittest.main()
