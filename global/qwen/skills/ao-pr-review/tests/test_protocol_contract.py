import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / 'SKILL.md').read_text()
README = (ROOT / 'README.md').read_text()
CONTRACT = (ROOT / 'references/contract.md').read_text()
POLICY = (ROOT / 'references/policy.md').read_text()
VERSION = (ROOT / 'VERSION').read_text().strip()


class ReviewProtocolContractTests(unittest.TestCase):
    def test_protocol_skill_is_not_invocable(self):
        self.assertIn('user-invocable: false', SKILL)
        self.assertIn('disable-model-invocation: true', SKILL)

    def test_version_and_high_only_publication_contract(self):
        self.assertEqual(VERSION, '0.4.0')
        for text in (SKILL, README, CONTRACT, POLICY):
            self.assertIn('high', text)
        self.assertIn('/review <canonical-url> --effort high', SKILL)
        self.assertIn('posting path is high-only', README)

    def test_two_phase_protocol_uses_normal_native_review_completion(self):
        for text in (SKILL, README, CONTRACT):
            self.assertIn('publish', text.lower())
            self.assertIn('discard', text.lower())
        self.assertIn('complete normally', SKILL)
        self.assertIn('including its own persistence and cleanup', README)
        self.assertIn("including Qwen's own persistence and cleanup behavior", CONTRACT)

    def test_discard_ack_does_not_claim_completed_review(self):
        self.assertIn('SEMANTIC_REVIEW_DISCARDED reviewSessionId reviewKey expectedHead', CONTRACT)
        self.assertNotIn('SEMANTIC_REVIEW_DISCARDED reviewSessionId reviewKey reviewedHead', CONTRACT)

    def test_head_drift_does_not_prescribe_qwen_state_cleanup(self):
        self.assertIn('reasonCode=head_moved', CONTRACT)
        self.assertIn('Qwen owns internal state handling', CONTRACT)
        self.assertNotIn('It cleans up and returns publication failure', CONTRACT)

    def test_no_executable_review_controller_remains(self):
        scripts = ROOT / 'scripts'
        if scripts.exists():
            self.assertEqual([p.name for p in scripts.iterdir() if p.is_file()], [])
        self.assertNotIn('result.json', CONTRACT)
        self.assertNotIn('select_review_effort', SKILL)

    def test_contract_uses_semantic_event_not_provider_event(self):
        self.assertIn('semanticEvent', CONTRACT)
        self.assertIn('Provider-side submission may neutralize', POLICY)


if __name__ == '__main__':
    unittest.main()
