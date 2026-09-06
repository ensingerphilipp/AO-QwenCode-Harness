#!/usr/bin/env python3
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "template-manifest.json").read_text())
TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class TemplateContractTests(unittest.TestCase):
    def test_manifest_version(self):
        self.assertEqual(MANIFEST["schemaVersion"], 1)

    def test_rendered_file_tokens_match_manifest_exactly(self):
        for rel, expected in MANIFEST["renderedFiles"].items():
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            found = TOKEN_RE.findall(path.read_text())
            self.assertEqual(sorted(found), sorted(expected), rel)
            self.assertEqual(len(found), len(set(found)), f"duplicate token in {rel}")

    def test_copy_files_exist_and_have_no_tokens(self):
        for rel in MANIFEST["copyFiles"]:
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            self.assertFalse(TOKEN_RE.search(path.read_text()), rel)

    def test_merge_fragments_are_safe_and_token_free(self):
        for source, destination in MANIFEST["mergeFragments"].items():
            path = ROOT / source
            self.assertTrue(path.is_file(), source)
            self.assertEqual(destination, ".gitignore")
            self.assertFalse(TOKEN_RE.search(path.read_text()), source)
        self.assertIn(".qwen/reviews/", (ROOT / "gitignore.harness.fragment").read_text())

    def test_default_lifecycle_config_enables_semantic_review(self):
        config = json.loads((ROOT / ".agent-harness.json").read_text())
        self.assertEqual(config, {
            "schemaVersion": 1,
            "semanticReview": {"enabled": True},
        })

    def test_default_review_risk_config_is_additive_empty(self):
        config = json.loads((ROOT / ".qwen/review-config.json").read_text())
        self.assertEqual(config, {
            "schemaVersion": 1,
            "highRiskPaths": [],
            "highRiskLabels": [],
        })

    def test_ci_delegates_to_standard_verification_entrypoint(self):
        workflow = (ROOT / ".github/workflows/verify.yml").read_text()
        self.assertIn("run: bash scripts/verify", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            workflow,
        )


    def test_workflow_tokens_support_a_valid_render_shape(self):
        workflow = (ROOT / ".github/workflows/verify.yml").read_text()
        rendered = workflow.replace("{{CI_RUNNER}}", "ubuntu-24.04")
        rendered = rendered.replace("{{CI_TIMEOUT_MINUTES}}", "30")
        rendered = rendered.replace(
            "{{CI_SETUP_STEPS}}",
            "      - name: Project toolchain setup\n        run: echo setup",
        )
        self.assertFalse(TOKEN_RE.search(rendered))
        self.assertIn("      - name: Project toolchain setup", rendered)
        self.assertIn("      - name: Verify", rendered)

    def test_required_generated_verification_file_is_declared(self):
        self.assertEqual(MANIFEST["requiredGeneratedFiles"], ["scripts/verify"])

    def test_templates_are_project_neutral(self):
        forbidden = ("Premiumizearr", "/home/opencode", "go.mod", "package-lock.json")
        for path in ROOT.rglob("*"):
            if (not path.is_file() or path.name == "test_template_contract.py"
                    or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}):
                continue
            text = path.read_text(encoding="utf-8")
            for term in forbidden:
                self.assertNotIn(term, text, f"{term!r} leaked into {path.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
