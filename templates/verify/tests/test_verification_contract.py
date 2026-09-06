#!/usr/bin/env python3
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC = ROOT / "generic"
TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
PROFILE_NAMES = ("go", "node", "python", "rust", "go-node")


class VerificationContractTests(unittest.TestCase):
    def test_generic_manifest_matches_script_tokens(self):
        manifest = json.loads((GENERIC / "manifest.json").read_text())
        script = (GENERIC / "scripts/verify").read_text()
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["output"], "scripts/verify")
        self.assertEqual(manifest["fileMode"], "0755")
        self.assertEqual(sorted(TOKEN_RE.findall(script)), sorted(manifest["requiredTokens"]))

    def test_profiles_declare_every_render_token_exactly(self):
        for name in PROFILE_NAMES:
            profile = json.loads((ROOT / name / "profile.json").read_text())
            templates = "\n".join(
                item["template"]
                for group in ("verificationCandidates", "ciSetupCandidates")
                for item in profile[group]
            )
            self.assertEqual(sorted(set(TOKEN_RE.findall(templates))), sorted(profile["requiredRenderTokens"]), name)

    def render_script(self, preflight, steps):
        script = (GENERIC / "scripts/verify").read_text()
        script = script.replace("{{VERIFY_PREFLIGHT}}", preflight)
        script = script.replace("{{VERIFY_STEPS}}", steps)
        self.assertFalse(TOKEN_RE.search(script))
        return script

    def run_rendered(self, steps):
        with tempfile.TemporaryDirectory(prefix="verify-contract-") as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            script = root / "verify"
            script.write_text(self.render_script("command -v git >/dev/null", steps))
            script.chmod(0o755)
            return subprocess.run(
                ["bash", str(script)], cwd=root, text=True,
                capture_output=True, check=False,
            )

    def test_rendered_script_passes_cleanly(self):
        result = self.run_rendered("printf 'check: ok\\n'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verify: PASS", result.stdout)

    def test_rendered_script_fails_fast(self):
        result = self.run_rendered("false\nprintf 'must-not-run\\n'")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify: FAILED", result.stderr)
        self.assertNotIn("must-not-run", result.stdout)

    def test_profiles_are_identity_consistent_and_project_neutral(self):
        forbidden = ("Premiumizearr", "/home/opencode")
        for name in PROFILE_NAMES:
            path = ROOT / name / "profile.json"
            profile = json.loads(path.read_text())
            self.assertEqual(profile["schemaVersion"], 1)
            self.assertEqual(profile["id"], name)
            text = path.read_text()
            for term in forbidden:
                self.assertNotIn(term, text)

    def test_generic_verifier_contains_no_repair_commands(self):
        script = (GENERIC / "scripts/verify").read_text()
        for prohibited in ("git add", "git commit", "git reset", "git checkout", "git clean"):
            self.assertNotIn(prohibited, script)


if __name__ == "__main__":
    unittest.main()
