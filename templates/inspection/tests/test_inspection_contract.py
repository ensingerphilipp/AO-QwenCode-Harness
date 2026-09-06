#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "config/project-inspection.schema.json").read_text())
MANIFEST = json.loads((ROOT / "templates/project/template-manifest.json").read_text())
PROMPT = (ROOT / "templates/prompts/inspect-project.md").read_text()


class InspectionContractTests(unittest.TestCase):
    def test_schema_requires_exact_project_template_tokens(self):
        expected = set()
        for tokens in MANIFEST["renderedFiles"].values():
            expected.update(tokens)
        template = SCHEMA["properties"]["templateValues"]
        self.assertEqual(set(template["required"]), expected)
        self.assertEqual(set(template["properties"]), expected)
        decision_enum = set(SCHEMA["$defs"]["decision"]["properties"]["affectedTokens"]["items"]["enum"])
        self.assertEqual(decision_enum, expected)

    def test_schema_profiles_match_available_profiles(self):
        expected = {"generic"}
        expected.update(
            json.loads(p.read_text())["id"]
            for p in (ROOT / "templates/verify").glob("*/profile.json")
        )
        actual = set(SCHEMA["properties"]["verification"]["properties"]["selectedProfiles"]["items"]["enum"])
        self.assertEqual(actual, expected)

    def test_prompt_is_explicitly_read_only_and_fail_closed(self):
        for phrase in (
            "This phase is read-only",
            "do not edit files",
            "do not edit files",
            "do not install dependencies",
            "set that token value to `null`",
            "Do not use `TBD`, guesses, generic filler, or invented policy",
            "Return one JSON object conforming exactly",
            "do not wrap it in Markdown fences",
        ):
            self.assertIn(phrase, PROMPT)

    def test_prompt_requires_evidence_and_real_verification(self):
        for phrase in (
            "Every asserted fact and every proposed verification command must cite repository evidence",
            "profiles only when repository evidence supports them",
            "bash scripts/verify",
            "contains no defensible required check",
        ):
            self.assertIn(phrase, PROMPT)


if __name__ == "__main__":
    unittest.main()
