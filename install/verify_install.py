#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    Path(".qwen/QWEN.md"): ROOT / "global/qwen/QWEN.md",
    Path(".ao/rules/agentRules.md"): ROOT / "global/ao/rules/agentRules.md",
    Path(".ao/rules/orchestratorRules.md"): ROOT / "global/ao/rules/orchestratorRules.md",
    Path(".ao/policies/semanticReviewPublication.md"): ROOT / "global/ao/policies/semanticReviewPublication.md",
    Path(".local/bin/ao-refresh-orchestrator"): ROOT / "lifecycle/ao-refresh-orchestrator",
}
SKILL = ROOT / "global/qwen/skills/ao-pr-review"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def expected_files() -> dict[Path, Path]:
    result = dict(EXPECTED)
    for source in SKILL.rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts and source.suffix not in {".pyc", ".pyo"}:
            result[Path(".qwen/skills/ao-pr-review") / source.relative_to(SKILL)] = source
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a host-global AO + Qwen Code harness installation.")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--skip-runtime-checks", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    failures = []

    for rel, source in expected_files().items():
        target = home / rel
        if not target.is_file() or target.is_symlink():
            failures.append(f"missing/non-regular: {target}")
        elif sha(target) != sha(source):
            failures.append(f"content mismatch: {target}")

    if not args.skip_runtime_checks:
        for command in ("git", "gh", "ao", "qwen", "python3"):
            if not shutil.which(command):
                failures.append(f"required command missing: {command}")
        if not failures:
            helper = home / ".qwen/skills/ao-pr-review/scripts/run_explicit_review.py"
            proc = subprocess.run(["python3", str(helper), "help"], text=True, capture_output=True, timeout=15)
            if proc.returncode != 0:
                failures.append(f"ao-pr-review help failed: {(proc.stderr or proc.stdout).strip()}")
            ao = subprocess.run(["ao", "project", "set-config", "--help"], text=True, capture_output=True, timeout=15)
            for flag in ("--config-json", "--agent-rules", "--orchestrator-rules", "--post-create"):
                if flag not in ao.stdout:
                    failures.append(f"AO project set-config lacks {flag}")
            review = subprocess.run(["qwen", "review", "run", "--help"], text=True, capture_output=True, timeout=15)
            for flag in ("--effort", "--json", "--fail-on", "--approval-mode", "--timeout-minutes"):
                if flag not in review.stdout:
                    failures.append(f"Qwen review run lacks {flag}")
            gh = subprocess.run(["gh", "pr", "checks", "--help"], text=True, capture_output=True, timeout=15)
            for flag in ("--required", "--json"):
                if flag not in gh.stdout:
                    failures.append(f"GitHub CLI pr checks lacks {flag}")

    if failures:
        print("verify-install: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Host-global harness installation verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
