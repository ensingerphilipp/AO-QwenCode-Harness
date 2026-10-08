from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
ORCH = (ROOT / 'global/ao/rules/orchestratorRules.md').read_text()
PUB = (ROOT / 'global/ao/policies/semanticReviewPublication.md').read_text()
AGENT = (ROOT / 'global/ao/rules/agentRules.md').read_text()


class AOPolicyContractTests(unittest.TestCase):
    def test_retired_skill_is_absent(self):
        self.assertFalse((ROOT / 'global/qwen/skills/ao-pr-review').exists())

    def test_semantic_toggle_remains_fail_closed(self):
        for phrase in ('.agent-harness.json', 'enabled by default', 'exactly `false` disables'):
            self.assertIn(phrase, ORCH)

    def test_rereview_queue_admission_requires_green_ci(self):
        # PR #105 held queue ticket 25 while fork checks were action_required.
        section = ORCH.split('## Acquire host-global review admission', 1)[1].split('## Review effort', 1)[0]
        self.assertIn('READY_FOR_REVIEW', section)
        self.assertIn('READY_FOR_REREVIEW', section)
        self.assertIn('action_required', section)
        self.assertIn('no queue ticket', section)
        self.assertIn('all required deterministic checks pass', section)
        self.assertLess(section.index('all required deterministic checks pass'), section.index('Re-qualify the head and CI before requesting admission'))

    def test_reviewer_spawn_is_explicit_chat_worker(self):
        command = 'ao spawn --project <PROJECT_ID> --kind worker --harness qwen --mode chat --name rev-pr-<NUMBER>'
        self.assertIn(command, ORCH)
        self.assertIn('MUST NOT be omitted or inferred', ORCH)

    def test_persistent_chat_native_review_is_the_only_backbone(self):
        for phrase in ('--kind worker --harness qwen --mode chat', '/review <canonical-PR-URL> --effort high', 'same idle reviewer'):
            self.assertIn(phrase, ORCH)
        for forbidden in ('run_explicit_review.py --monitor-envelope', 'resultJson', 'Qwen Monitor while the review runs'):
            self.assertNotIn(forbidden, ORCH)

    def test_generic_worker_completion_contract_is_preserved(self):
        for phrase in ('TASK_COMPLETE', 'REVIEW_HANDOFF_BLOCKED', 'freeform, host-level, read-only, no-change'):
            self.assertIn(phrase, AGENT)
        self.assertIn('READY_FOR_REVIEW', AGENT)
        self.assertIn('READY_FOR_REREVIEW', AGENT)

    def test_ao_review_effort_is_fixed_high(self):
        self.assertIn('MUST run at native `high` effort', ORCH)
        self.assertIn('/review <ASSIGNED_CANONICAL_URL> --effort high', AGENT)
        self.assertIn('selected effort MUST be `high`', AGENT)

    def test_verdict_phase_preflights_native_posting_authorization(self):
        self.assertIn('qwen review parse-args --stdin', ORCH)
        self.assertIn('comment.effective == false', ORCH)
        self.assertIn('review.comment: true', ORCH)
        self.assertIn('comment.effective == false', AGENT)
        self.assertIn('comment.effective == false', PUB)

    def test_nonposting_preflight_precedes_reviewer_creation_everywhere(self):
        self.assertLess(ORCH.index('## Non-posting verdict preflight'), ORCH.index('## Dispatch the persistent reviewer'))
        self.assertLess(PUB.index('run the deterministic non-posting argument preflight'), PUB.index('Create one persistent Chat/ACP reviewer'))

    def test_setup_protocol_text_is_not_publication_authorization(self):
        self.assertIn('setup assignment is descriptive protocol context only', ORCH)
        self.assertIn('MUST NOT be interpreted as a request to publish', ORCH)
        self.assertIn('Treat the setup assignment as protocol description only, not publication authorization', AGENT)
        self.assertIn('Only a later standalone `AO_SEMANTIC_REVIEW_PUBLISH`', AGENT)

    def test_two_phase_authorization_defers_native_cleanup(self):
        for phrase in ('AO_SEMANTIC_REVIEW_PUBLISH', 'AO_SEMANTIC_REVIEW_DISCARD'):
            self.assertIn(phrase, ORCH)
            self.assertIn(phrase, AGENT)
        self.assertIn('publication decision is intentionally unresolved', AGENT)
        self.assertIn('defers Step 9 cleanup', ORCH)
        self.assertIn('DEFER Step 9', AGENT)
        self.assertIn('Only after native `post comments` submit succeeds', AGENT)
        self.assertIn('Publication failure retains evidence', PUB)
        self.assertIn('same reviewer session is still reserved for the publication decision', ORCH)

    def test_native_qwen_owns_publication_projection(self):
        for phrase in ('native GitHub review publication', 'event, body, inline comments', 'AO MUST NOT create a second findings summary'):
            self.assertIn(phrase, PUB)
        self.assertIn('ao-semantic-review-summary:v1', PUB)
        self.assertIn('retired', PUB)

    def test_no_automatic_resume_or_replacement_reviewer(self):
        self.assertIn('There is no automatic timeout/resume path', ORCH)
        self.assertIn('Never spawn a replacement reviewer', ORCH)

    def test_chat_control_delivery_is_idempotent(self):
        for phrase in ('--client-message-id', '--recover-only', 'must never become a second `post comments` turn'):
            self.assertIn(phrase, ORCH)

    def test_next_phase_starts_only_from_idle_chat(self):
        self.assertIn('reviewer Chat session **idle**', ORCH)
        self.assertIn('`--steer` against a working Chat turn', ORCH)
        self.assertIn('end the Chat turn immediately', AGENT)

    def test_publish_time_head_drift_aborts_without_restart(self):
        self.assertIn('reasonCode=head_moved', ORCH)
        self.assertIn('do not publish the old verdict', AGENT)
        self.assertIn('do not convert this authorization into a review of the new SHA', AGENT)

    def test_reviewer_messages_are_explicit(self):
        for phrase in (
            'SEMANTIC_REVIEW_READY', 'SEMANTIC_REVIEW_RESULT',
            'SEMANTIC_REVIEW_PUBLISHED', 'SEMANTIC_REVIEW_DISCARDED',
            'SEMANTIC_REVIEW_PUBLICATION_FAILURE',
        ):
            self.assertIn(phrase, AGENT)

    def test_worker_repairs_from_native_review_not_ao_projection(self):
        self.assertIn('native Qwen GitHub review', AGENT)
        self.assertIn('AO does not copy/rewrite the findings', ORCH)
        self.assertNotIn('addressedFindingIds', AGENT)

    def test_ao_owns_status_not_summary_comment(self):
        self.assertIn('`ao/semantic-review` commit status', ORCH)
        self.assertIn('no longer creates or updates an AO semantic-review summary comment', ORCH)
        self.assertIn('normal lifecycle creates or updates none', PUB)


if __name__ == '__main__':
    unittest.main()
