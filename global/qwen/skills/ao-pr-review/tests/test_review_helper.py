#!/usr/bin/env python3
"""Unit tests for the ao-pr-review helper.

Everything runs against fake `gh` and `qwen` executables in a temporary
directory: no real GitHub, no real review, no network. The helper is
exercised as a real subprocess so CLI behavior, exit codes, and output
lines are tested end to end.
"""

import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
HELPER = SKILL_ROOT / "scripts" / "run_explicit_review.py"
FIXTURE = SKILL_ROOT / "tests" / "fixtures" / "qwen-review-artifact-v1.json"

VALID_SHA = "a" * 40
DRIFT_SHA = "b" * 40
OTHER_SHA = "c" * 40

SKIP = object()

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys

def log(argv):
    p = os.environ.get("FAKE_LOG")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"exe": "gh", "argv": argv}) + "\n")

def load():
    with open(os.environ["FAKE_GH_STATE"], encoding="utf-8") as f:
        return json.load(f)

def save(state):
    with open(os.environ["FAKE_GH_STATE"], "w", encoding="utf-8") as f:
        json.dump(state, f)

def out(obj, exit_code=0):
    print(json.dumps(obj))
    sys.exit(exit_code)

def main():
    argv = sys.argv[1:]
    log(argv)
    state = load()
    if argv[:2] == ["repo", "view"]:
        if state.get("repo_view_fail"):
            sys.stderr.write("simulated repo view failure\n")
            sys.exit(1)
        out({"owner": {"login": state["repo_owner"]}, "name": state["repo_name"]})
    if argv[:2] == ["pr", "view"]:
        pr = dict(state["pr"])
        seq = state.get("head_sequence")
        if seq is not None:
            idx = state.get("head_idx", 0)
            fails = state.get("head_read_failures") or []
            if idx < len(fails) and fails[idx]:
                sys.stderr.write("simulated head read failure\n")
                sys.exit(1)
            pr["headRefOid"] = seq[min(idx, len(seq) - 1)]
            state["head_idx"] = idx + 1
            save(state)
        out(pr)
    if argv[:2] == ["pr", "checks"]:
        if state.get("checks_fail"):
            sys.stderr.write("simulated checks failure\n")
            sys.exit(1)
        # Optional nonzero exit with parseable stdout (real gh exits 1 for
        # a failed required check and 8 for pending checks while stdout
        # still carries the JSON array).
        checks_exit = state.get("checks_exit", 0)
        if checks_exit:
            sys.stderr.write("simulated checks nonzero exit\n")
        out(state.get("checks", []), checks_exit)
    if argv[:2] == ["pr", "diff"]:
        if state.get("diff_fail"):
            sys.stderr.write("simulated diff failure\n")
            sys.exit(1)
        sys.stdout.write(state.get("diff", ""))
        sys.exit(0)
    sys.stderr.write("fake gh: unsupported argv: %r\n" % (argv,))
    sys.exit(1)

main()
'''

FAKE_QWEN = r'''#!/usr/bin/env python3
import json, os, shutil, subprocess, sys, time

def log(argv):
    p = os.environ.get("FAKE_LOG")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"exe": "qwen", "argv": argv}) + "\n")

def load():
    with open(os.environ["FAKE_QWEN_STATE"], encoding="utf-8") as f:
        return json.load(f)

def main():
    argv = sys.argv[1:]
    log(argv)
    state = load()
    if argv == ["--version"]:
        print(state.get("qwen_version", "qwen 0.22.3"))
        sys.exit(0)
    if argv[:2] == ["review", "run"]:
        report_dir = state.get("report_dir")
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
            with open(os.path.join(report_dir, "report.md"), "w", encoding="utf-8") as f:
                f.write(state.get("report_md_content", "# review\n"))
            if state.get("report_json_raw") is not None:
                with open(os.path.join(report_dir, "report.json"), "w", encoding="utf-8") as f:
                    f.write(state["report_json_raw"])
            elif "report_json_content" in state:
                with open(os.path.join(report_dir, "report.json"), "w", encoding="utf-8") as f:
                    json.dump(state["report_json_content"], f)
        if state.get("mutate_tracked_file"):
            with open(state["mutate_tracked_file"], "a", encoding="utf-8") as f:
                f.write("// simulated tracked mutation\n")
        if state.get("mutate_and_commit"):
            with open(state["mutate_and_commit"], "a", encoding="utf-8") as f:
                f.write("// simulated tracked mutation with local commit\n")
            subprocess.run(
                ["git", "add", state["mutate_and_commit"]], check=False
            )
            subprocess.run(
                ["git", "commit", "-m", "simulated local commit"],
                check=False,
            )
        if state.get("switch_branch"):
            subprocess.run(
                ["git", "checkout", "-b", state["switch_branch"]],
                check=False,
            )
        if state.get("delete_git_dir"):
            shutil.rmtree(".git", ignore_errors=True)
        if state.get("create_untracked_file"):
            with open(state["create_untracked_file"], "w", encoding="utf-8") as f:
                f.write("simulated untracked review artifact\n")
        if state.get("sleep_seconds"):
            time.sleep(state["sleep_seconds"])
        if state.get("wrapper_stdout"):
            sys.stdout.write(state["wrapper_stdout"])
        if state.get("qwen_stderr"):
            sys.stderr.write(state["qwen_stderr"])
        sys.exit(state.get("qwen_exit", 0))
    sys.stderr.write("fake qwen: unsupported argv: %r\n" % (argv,))
    sys.exit(1)

main()
'''


def default_counts():
    return {
        "total": 0,
        "bySeverity": {"Critical": 0, "Suggestion": 0, "Nice to have": 0},
        "byConfidence": {"high": 0, "low": 0},
        "held": 0,
        "byOutcome": {"fixed": 0, "skipped": 0, "no_change_needed": 0},
    }


# v0.22.3 required finding fields that tests do not care about; filled in
# with valid defaults so tests can stay focused on the field under test.
FINDING_DEFAULTS = {
    "source": "review",
    "shortSummary": "short summary",
    "locations": [{"file": "internal/app/app.go"}],
}


def make_companion(*, schema_version=1, target="pr-5", effort="medium",
                   event="COMMENT", base_event="APPROVE", capped_by=None,
                   findings=None, counts=None, verdict_line=None,
                   markdown_report_path=None):
    findings = [] if findings is None else list(findings)
    for finding in findings:
        if isinstance(finding, dict):
            for key, value in FINDING_DEFAULTS.items():
                finding.setdefault(key, value)
    doc = {
        "schemaVersion": schema_version,
        "target": target,
        "effort": effort,
        "verdict": {
            "event": event,
            "verdictLine": (
                verdict_line
                if verdict_line is not None
                else f"{event} on {target} (base event {base_event})"
            ),
            "baseEvent": base_event,
            "cappedBy": [] if capped_by is None else capped_by,
        },
        "findings": findings,
        "counts": default_counts() if counts is None else counts,
        "markdownReportPath": (
            ".qwen/reviews/pr-5-review.md"
            if markdown_report_path is None
            else markdown_report_path
        ),
    }
    return doc


def make_wrapper(*, report_path, event="COMMENT", base_event="APPROVE",
                 capped_by=None, completed=True, timed_out=False):
    return {
        "completed": completed,
        "timedOut": timed_out,
        "reportPath": report_path,
        "event": event,
        "baseEvent": base_event,
        "cappedBy": [] if capped_by is None else capped_by,
    }


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_helper_module():
    spec = importlib.util.spec_from_file_location("aopr_helper", str(HELPER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HelperBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aopr-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.helper = HELPER
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state_root = self.tmp / "state"
        self.state_root.mkdir()
        self.report_dir = self.tmp / "reports"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.log_path = self.tmp / "invocations.log"
        self.gh_state_path = self.tmp / "gh-state.json"
        self.qwen_state_path = self.tmp / "qwen-state.json"
        (self.bin / "gh").write_text(FAKE_GH)
        (self.bin / "gh").chmod(0o755)
        (self.bin / "qwen").write_text(FAKE_QWEN)
        (self.bin / "qwen").chmod(0o755)
        self.init_repo()
        self.set_gh()
        self.set_qwen()
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_LOG": str(self.log_path),
            "FAKE_GH_STATE": str(self.gh_state_path),
            "FAKE_QWEN_STATE": str(self.qwen_state_path),
            "AO_PR_REVIEW_STATE_DIR": str(self.state_root),
        }

    # -- repository ---------------------------------------------------------

    def git(self, *args):
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"git {' '.join(args)} failed: {proc.stderr}",
        )
        return proc

    def init_repo(self):
        self.git("init", "-b", "main")
        self.git("config", "user.email", "ao-pr-review-test@example.com")
        self.git("config", "user.name", "AO PR Review Test")
        (self.repo / "app.go").write_text("package main\n\nfunc main() {}\n")
        (self.repo / "README.md").write_text("# demo app\n")
        (self.repo / ".qwen").mkdir()
        (self.repo / ".qwen" / "review-config.json").write_text(json.dumps({
            "schemaVersion": 1,
            "highRiskPaths": [
                "go.mod", "go.sum", "web/package.json", "web/package-lock.json",
                "Dockerfile*", ".goreleaser*", "pkg/**", "internal/config/**"
            ],
            "highRiskLabels": []
        }) + "\n")
        self.git("add", "app.go", "README.md", ".qwen/review-config.json")
        self.git("commit", "-m", "baseline")

    # -- fakes --------------------------------------------------------------

    def default_pr_doc(self):
        # Real gh 2.98.0 shape: flattened files/labels arrays.
        return {
            "number": 5,
            "url": "https://github.com/acme/demo/pull/5",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": VALID_SHA,
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "files": [
                {"path": "internal/app/app.go"},
                {"path": "internal/app/app_test.go"},
            ],
            "labels": [],
            "title": "Fix off-by-one in counter",
            "body": "Routine fix for the counter off-by-one.",
        }

    def set_gh(self, *, repo_owner="acme", repo_name="demo", pr=None,
               checks=None, diff=None, **kw):
        state = {
            "repo_owner": repo_owner,
            "repo_name": repo_name,
            "pr": pr if pr is not None else self.default_pr_doc(),
            "checks": checks if checks is not None else [
                {"name": "verify", "state": "completed", "bucket": "pass"},
                {"name": "lint", "state": "completed", "bucket": "pass"},
            ],
            "diff": diff if diff is not None else (
                "diff --git a/internal/app/app.go b/internal/app/app.go\n"
                "--- a/internal/app/app.go\n"
                "+++ b/internal/app/app.go\n"
                "@@ -1 +1 @@\n"
                "- return n\n"
                "+ return n + 1\n"
            ),
        }
        state.update(kw)
        self.gh_state_path.write_text(json.dumps(state))

    def set_qwen(self, wrapper_stdout=None, report_md_content="# review report\n",
                 report_json_content=SKIP, report_json_raw=None,
                 qwen_exit=0, qwen_version="qwen 0.22.3", qwen_stderr="",
                 mutate_tracked_file=None, create_untracked_file=None,
                 mutate_and_commit=None, delete_git_dir=None,
                 switch_branch=None, report_dir=None, sleep_seconds=None):
        state = {
            "qwen_version": qwen_version,
            "qwen_exit": qwen_exit,
            "qwen_stderr": qwen_stderr,
            "report_dir": str(
                Path(report_dir) if report_dir is not None else self.report_dir
            ),
            "report_md_content": report_md_content,
        }
        if report_json_raw is not None:
            state["report_json_raw"] = report_json_raw
        elif report_json_content is not SKIP:
            state["report_json_content"] = report_json_content
        if wrapper_stdout is not None:
            state["wrapper_stdout"] = wrapper_stdout
        if sleep_seconds is not None:
            state["sleep_seconds"] = sleep_seconds
        if mutate_tracked_file is not None:
            state["mutate_tracked_file"] = str(mutate_tracked_file)
        if mutate_and_commit is not None:
            state["mutate_and_commit"] = str(mutate_and_commit)
        if delete_git_dir:
            state["delete_git_dir"] = True
        if switch_branch is not None:
            state["switch_branch"] = switch_branch
        if create_untracked_file is not None:
            state["create_untracked_file"] = str(create_untracked_file)
        self.qwen_state_path.write_text(json.dumps(state))

    def setup_success(self, *, pr=None, gh_kw=None, wrapper=None,
                      companion=None, qwen_exit=None, qwen_stderr="",
                      mutate_tracked_file=None, create_untracked_file=None,
                      mutate_and_commit=None, delete_git_dir=None,
                      switch_branch=None, report_dir=None,
                      sleep_seconds=None):
        """Configure fakes for a full run; explicit wrapper/companion win."""
        report_dir = Path(report_dir) if report_dir is not None else self.report_dir
        companion = make_companion() if companion is None else companion
        if wrapper is None:
            wrapper = make_wrapper(report_path=str(report_dir / "report.md"))
        if qwen_exit is None:
            verdict = companion.get("verdict") if isinstance(companion, dict) else {}
            qwen_exit = 3 if verdict.get("event") == "REQUEST_CHANGES" else 0
        self.set_qwen(
            wrapper_stdout=json.dumps(wrapper),
            report_json_content=companion,
            qwen_exit=qwen_exit,
            qwen_stderr=qwen_stderr,
            mutate_tracked_file=mutate_tracked_file,
            create_untracked_file=create_untracked_file,
            mutate_and_commit=mutate_and_commit,
            delete_git_dir=delete_git_dir,
            switch_branch=switch_branch,
            report_dir=report_dir,
            sleep_seconds=sleep_seconds,
        )
        if pr is not None or gh_kw:
            self.set_gh(pr=pr, **(gh_kw or {}))
        return wrapper, companion

    def setup_for_disposition(self, disposition):
        """Configure fakes so a full run ends in the given disposition."""
        if disposition == "pass":
            self.setup_success()
        elif disposition == "blocked":
            self.setup_success(
                companion=make_companion(event="REQUEST_CHANGES"),
                wrapper=make_wrapper(
                    report_path=str(self.report_dir / "report.md"),
                    event="REQUEST_CHANGES",
                ),
            )
        elif disposition == "stale":
            # reads: resolve, early pre, final pre (immediately before
            # launch), post-review
            self.setup_success(
                gh_kw={"head_sequence": [
                    VALID_SHA, VALID_SHA, VALID_SHA, DRIFT_SHA
                ]}
            )
        elif disposition == "needs_human":
            self.setup_success(
                companion=make_companion(
                    findings=[
                        {
                            "id": "R1",
                            "severity": "Critical",
                            "confidence": "low",
                            "summary": "s",
                            "failureScenario": "f",
                        }
                    ]
                )
            )
        elif disposition == "review_error":
            self.setup_success(gh_kw={"checks_fail": True})
        else:
            raise ValueError(disposition)

    # -- runner / observation helpers ---------------------------------------

    def run_helper(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(self.helper), *args],
            env=self.env,
            cwd=str(cwd if cwd is not None else self.repo),
            capture_output=True,
            text=True,
            timeout=300,
        )

    def invocation_log(self):
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def invocations(self, exe, *prefix):
        return [
            entry["argv"]
            for entry in self.invocation_log()
            if entry["exe"] == exe
            and entry["argv"][: len(prefix)] == list(prefix)
        ]

    def run_dirs(self):
        runs = self.state_root / "runs"
        if not runs.is_dir():
            return []
        return sorted(p for p in runs.glob("*/*/*") if p.is_dir())

    def read_result(self, index=-1):
        paths = self.run_dirs()
        self.assertTrue(paths, "expected an evidence run directory")
        result_file = paths[index] / "result.json"
        self.assertTrue(result_file.is_file(), f"no result.json under {paths[index]}")
        return json.loads(result_file.read_text(encoding="utf-8"))

    def qwen_review_argv(self):
        calls = self.invocations("qwen", "review", "run")
        self.assertEqual(len(calls), 1, "expected exactly one native review")
        return calls[0]


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


class TestHelpAndUsage(HelperBase):
    def test_help_exits_zero_without_invoking_fakes(self):
        result = self.run_helper("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage (skill):", result.stdout)
        self.assertIn(
            "/ao-pr-review <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> "
            "[auto|medium|high]",
            result.stdout,
        )
        self.assertFalse(self.log_path.exists(), "help must not invoke gh/qwen")

    def test_no_arguments_is_usage_error(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage error", result.stderr)

    def test_one_positional_is_usage_error(self):
        result = self.run_helper("5")
        self.assertEqual(result.returncode, 2)

    def test_four_positionals_is_usage_error(self):
        result = self.run_helper("5", VALID_SHA, "auto", "extra")
        self.assertEqual(result.returncode, 2)

    def test_invalid_effort_is_usage_error(self):
        result = self.run_helper("5", VALID_SHA, "turbo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid effort", result.stderr)

    def test_invalid_sha_too_short_is_usage_error(self):
        result = self.run_helper("5", "abc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("40 lowercase hexadecimal", result.stderr)

    def test_invalid_sha_uppercase_is_usage_error(self):
        result = self.run_helper("5", VALID_SHA.upper())
        self.assertEqual(result.returncode, 2)

    def test_non_github_url_is_usage_error(self):
        result = self.run_helper(
            "https://gitlab.com/acme/demo/-/merge_requests/5", VALID_SHA
        )
        self.assertEqual(result.returncode, 2)

    def test_args_file_preserves_pr_sha_and_effort(self):
        args_file = self.tmp / "args.txt"
        args_file.write_text(f"5 {VALID_SHA} high\n", encoding="utf-8")
        self.setup_success(companion=make_companion(effort="high"))
        result = self.run_helper("--args-file", str(args_file))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "high")
        self.assertEqual(doc["prNumber"], 5)
        self.assertEqual(doc["expectedHead"], VALID_SHA)
        self.assertEqual(self.invocations("gh", "pr", "view")[0][2], "5")

    def test_args_file_help_exits_zero(self):
        args_file = self.tmp / "args.txt"
        args_file.write_text("help\n", encoding="utf-8")
        result = self.run_helper("--args-file", str(args_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage (skill):", result.stdout)
        self.assertFalse(self.log_path.exists())

    def test_args_file_missing_is_usage_error(self):
        result = self.run_helper("--args-file", str(self.tmp / "nope.txt"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage error", result.stderr)

    def test_args_file_directory_is_usage_error(self):
        result = self.run_helper("--args-file", str(self.tmp))
        self.assertEqual(result.returncode, 2)

    def test_args_file_symlink_is_usage_error(self):
        real = self.tmp / "real.txt"
        real.write_text(f"5 {VALID_SHA}\n", encoding="utf-8")
        link = self.tmp / "link.txt"
        link.symlink_to(real)
        result = self.run_helper("--args-file", str(link))
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr)

    def test_args_file_oversized_is_usage_error(self):
        big = self.tmp / "big.txt"
        big.write_text(("5 " + VALID_SHA + " " + "x" * 5000) + "\n", encoding="utf-8")
        result = self.run_helper("--args-file", str(big))
        self.assertEqual(result.returncode, 2)
        self.assertIn("byte limit", result.stderr)

    def test_args_file_malformed_is_usage_error(self):
        bad = self.tmp / "bad.txt"
        bad.write_text('"5 ' + VALID_SHA + '\n', encoding="utf-8")
        result = self.run_helper("--args-file", str(bad))
        self.assertEqual(result.returncode, 2)
        self.assertIn("malformed", result.stderr)

    def test_args_file_empty_is_usage_error(self):
        empty = self.tmp / "empty.txt"
        empty.write_text("", encoding="utf-8")
        result = self.run_helper("--args-file", str(empty))
        self.assertEqual(result.returncode, 2)
        self.assertIn("empty or stale-looking", result.stderr)

    def test_args_file_whitespace_only_is_usage_error(self):
        ws = self.tmp / "ws.txt"
        ws.write_text("   \n\t\n", encoding="utf-8")
        result = self.run_helper("--args-file", str(ws))
        self.assertEqual(result.returncode, 2)
        self.assertIn("empty or stale-looking", result.stderr)

    def test_args_file_too_many_args_is_usage_error(self):
        many = self.tmp / "many.txt"
        many.write_text("1 2 3 4\n", encoding="utf-8")
        result = self.run_helper("--args-file", str(many))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported number of arguments", result.stderr)

    def test_args_file_single_non_help_token_is_usage_error(self):
        one = self.tmp / "one.txt"
        one.write_text("5\n", encoding="utf-8")
        result = self.run_helper("--args-file", str(one))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("usage error", result.stderr)
        self.assertNotIn("Traceback", result.stderr, "no traceback may leak")
        self.assertFalse(
            self.log_path.exists(), "neither gh nor qwen may be invoked"
        )

    def test_default_effort_request_is_auto(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "auto")
        self.assertIn(doc["selectedEffort"], ("medium", "high"))


# ---------------------------------------------------------------------------
# Target identity and repository binding
# ---------------------------------------------------------------------------


class TestTargetIdentity(HelperBase):
    def test_numeric_pr_in_current_repo(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["prNumber"], 5)
        self.assertEqual(doc["prUrl"], "https://github.com/acme/demo/pull/5")
        self.assertEqual(doc["repository"], "acme/demo")
        # the canonical URL is used for checks, head re-reads, diff
        for argv in self.invocations("gh", "pr", "checks"):
            self.assertEqual(argv[2], "https://github.com/acme/demo/pull/5")
        self.assertEqual(
            self.qwen_review_argv()[2], "https://github.com/acme/demo/pull/5"
        )

    def test_canonical_url_retained_through_every_command(self):
        url = "https://github.com/acme/demo/pull/5"
        self.setup_success()
        result = self.run_helper(url, VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        view_urls = [argv[2] for argv in self.invocations("gh", "pr", "view")]
        self.assertEqual(
            set(view_urls), {url}, "head re-reads must use the canonical URL"
        )
        for argv in self.invocations("gh", "pr", "checks"):
            self.assertEqual(argv[2], url)
        for argv in self.invocations("gh", "pr", "diff"):
            self.assertEqual(argv[2], url)
        self.assertEqual(self.qwen_review_argv()[2], url)

    def test_cross_repository_url_refused(self):
        pr = self.default_pr_doc()
        pr["number"] = 7
        pr["url"] = "https://github.com/acme/other/pull/7"
        self.setup_success(pr=pr)
        result = self.run_helper("https://github.com/acme/other/pull/7", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("differs from current repository", doc["error"])
        self.assertIn("orchestrator", doc["nextAction"])
        self.assertEqual(doc["repository"], "acme/other")
        # evidence lives under the PR's base repository
        self.assertTrue(
            (self.state_root / "runs" / "acme__other" / "pr-7").is_dir()
        )
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_fork_pr_uses_base_repository_identity(self):
        pr = self.default_pr_doc()
        pr["headRepository"] = {"login": "forker", "name": "demo"}
        self.setup_success(pr=pr)
        result = self.run_helper("https://github.com/acme/demo/pull/5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["repository"], "acme/demo")
        self.assertEqual(doc["prUrl"], "https://github.com/acme/demo/pull/5")

    def test_resolved_url_not_canonical_is_review_error(self):
        pr = self.default_pr_doc()
        pr["url"] = "https://example.com/not-github/pull/5"
        self.setup_success(pr=pr)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        self.assertIn("canonical", result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


class TestPreconditions(HelperBase):
    def test_closed_pr_refused(self):
        pr = self.default_pr_doc()
        pr["state"] = "CLOSED"
        self.setup_success(pr=pr)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("not OPEN", doc["error"])
        self.assertEqual(self.invocations("gh", "pr", "checks"), [])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_draft_pr_refused(self):
        pr = self.default_pr_doc()
        pr["isDraft"] = True
        self.setup_success(pr=pr)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("draft", doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_initial_head_mismatch_refused_before_ci(self):
        pr = self.default_pr_doc()
        pr["headRefOid"] = DRIFT_SHA
        self.setup_success(pr=pr)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("does not match", doc["error"])
        self.assertEqual(self.invocations("gh", "pr", "checks"), [])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_no_required_checks_refused(self):
        self.setup_success(gh_kw={"checks": []})
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("no required checks", doc["error"])
        self.assertEqual(doc["requiredChecks"]["checks"], [])
        self.assertEqual(doc["requiredChecks"]["exitCode"], 0)
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_pending_required_check_refused(self):
        self.setup_success(
            gh_kw={"checks": [{"name": "ci", "state": "queued", "bucket": "pending"}]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("not all passing", doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_failed_required_check_refused_with_snapshot(self):
        self.setup_success(
            gh_kw={
                "checks": [
                    {"name": "ci", "state": "completed", "bucket": "pass"},
                    {"name": "verify", "state": "completed", "bucket": "fail"},
                ]
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        snapshot = doc["requiredChecks"]
        self.assertEqual(snapshot["exitCode"], 0)
        self.assertEqual(
            [c["name"] for c in snapshot["checks"]], ["ci", "verify"]
        )
        self.assertEqual(
            snapshot["command"],
            "gh pr checks https://github.com/acme/demo/pull/5 "
            "--required --json name,state,bucket",
        )

    def test_checks_command_failure_preserved(self):
        self.setup_success(gh_kw={"checks_fail": True})
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["requiredChecks"]["exitCode"], 1)
        self.assertEqual(doc["requiredChecks"]["checks"], [])
        self.assertEqual(doc["disposition"], "review_error")

    def test_head_drift_after_ci_before_qwen_refused(self):
        self.setup_success(
            gh_kw={"head_sequence": [VALID_SHA, DRIFT_SHA, VALID_SHA]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("head moved after CI checks", doc["error"])
        self.assertEqual(doc["observedHeadAtResolve"], VALID_SHA)
        self.assertEqual(doc["observedHeadBefore"], DRIFT_SHA)
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_required_checks_snapshot_retained_on_success(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(
            [c["name"] for c in doc["requiredChecks"]["checks"]],
            ["verify", "lint"],
        )
        self.assertTrue(
            all(c["bucket"] == "pass" for c in doc["requiredChecks"]["checks"])
        )


# ---------------------------------------------------------------------------
# v0.2.6: prerequisite selection — the exact ao/semantic-review context is
# excluded; only required DETERMINISTIC checks gate the preflight. These
# tests do not authorize reviews, duplicate reviews, or override verdicts.
# ---------------------------------------------------------------------------


class TestSemanticReviewContextExclusion(HelperBase):
    SEMANTIC_CONTEXT = "ao/semantic-review"

    def semantic(self, state, bucket):
        return {"name": self.SEMANTIC_CONTEXT, "state": state, "bucket": bucket}

    def deterministic(self, name="verify", state="completed", bucket="pass"):
        return {"name": name, "state": state, "bucket": bucket}

    def assert_allowed(self, result, expected_names):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "pass")
        # the full snapshot (semantic entry included) is retained as evidence
        self.assertEqual(
            [c["name"] for c in doc["requiredChecks"]["checks"]],
            expected_names,
        )
        self.qwen_review_argv()  # exactly one native review was launched

    def assert_refused(self, result, error_needle):
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn(error_needle, doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    # -- all deterministic checks passing: the excluded context never gates

    def test_allowed_when_semantic_status_absent(self):
        self.setup_success(gh_kw={"checks": [self.deterministic()]})
        result = self.run_helper("5", VALID_SHA)
        self.assert_allowed(result, ["verify"])

    def test_allowed_when_semantic_status_pending(self):
        self.setup_success(
            gh_kw={"checks": [self.deterministic(), self.semantic("queued", "pending")]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_allowed(result, ["verify", "ao/semantic-review"])

    def test_allowed_when_semantic_status_failure(self):
        self.setup_success(
            gh_kw={"checks": [self.deterministic(), self.semantic("completed", "fail")]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_allowed(result, ["verify", "ao/semantic-review"])

    def test_allowed_when_semantic_status_error(self):
        self.setup_success(
            gh_kw={"checks": [self.deterministic(), self.semantic("completed", "error")]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_allowed(result, ["verify", "ao/semantic-review"])

    def test_allowed_when_gh_nonzero_exit_and_deterministic_all_pass(self):
        # gh exit 8 (a required check is pending) because ONLY the excluded
        # semantic context is pending; the deterministic set is non-empty
        # and fully passing, so the preflight must allow.
        self.setup_success(
            gh_kw={
                "checks": [
                    self.deterministic(),
                    self.semantic("queued", "pending"),
                ],
                "checks_exit": 8,
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_allowed(result, ["verify", "ao/semantic-review"])
        snapshot = self.read_result()["requiredChecks"]
        # nonzero exit and stderr are retained as evidence
        self.assertEqual(snapshot["exitCode"], 8)
        self.assertTrue(snapshot.get("stderr"))

    # -- a pending or failed DETERMINISTIC check refuses, even with the
    #    semantic entry present (and passing)

    def test_refused_when_deterministic_check_pending(self):
        self.setup_success(
            gh_kw={
                "checks": [
                    self.deterministic("ci", "queued", "pending"),
                    self.semantic("completed", "pass"),
                ]
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_refused(result, "not all passing")

    def test_refused_when_deterministic_check_failed(self):
        self.setup_success(
            gh_kw={
                "checks": [
                    self.deterministic(),
                    self.deterministic("ci", "completed", "fail"),
                    self.semantic("completed", "pass"),
                ]
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_refused(result, "not all passing")

    # -- empty deterministic set and unusable snapshot refuse (fail closed)

    def test_refused_when_only_semantic_entries_reported(self):
        self.setup_success(
            gh_kw={"checks": [self.semantic("completed", "pass")]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_refused(result, "no required deterministic checks")
        doc = self.read_result()
        self.assertEqual(doc["requiredChecks"]["exitCode"], 0)
        self.assertEqual(
            [c["name"] for c in doc["requiredChecks"]["checks"]],
            ["ao/semantic-review"],
        )

    def test_refused_when_checks_discovery_fails_with_unparseable_stdout(self):
        # gh nonzero exit with no parseable stdout (simulated auth error)
        self.setup_success(gh_kw={"checks_fail": True})
        result = self.run_helper("5", VALID_SHA)
        self.assert_refused(result, "unusable")
        snapshot = self.read_result()["requiredChecks"]
        self.assertEqual(snapshot["exitCode"], 1)
        self.assertEqual(snapshot["checks"], [])
        self.assertIn("parseError", snapshot)

    # -- similarly named contexts are NEVER excluded (exact match only)

    def test_similarly_named_failed_checks_refuse(self):
        for name in ("ao/semantic-review-v2", "semantic-review"):
            with self.subTest(name=name):
                self.setup_success(
                    gh_kw={
                        "checks": [
                            self.deterministic(),
                            {"name": name, "state": "completed", "bucket": "fail"},
                            self.semantic("completed", "pass"),
                        ]
                    }
                )
                result = self.run_helper("5", VALID_SHA)
                self.assert_refused(result, "not all passing")

    # -- malformed entries are never excluded: fail closed in precondition 7

    def test_malformed_entries_are_deterministic_and_fail_closed(self):
        self.setup_success(
            gh_kw={
                "checks": [
                    self.deterministic(),
                    "ao/semantic-review",  # non-dict entry
                    {},                    # dict without a string name
                ]
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assert_refused(result, "not all passing")


# ---------------------------------------------------------------------------
# Deterministic effort policy
# ---------------------------------------------------------------------------


def routine_view(**overrides):
    view = {
        "filePaths": ["internal/app/app.go"],
        "labels": [],
        "changedFiles": 1,
        "additions": 5,
        "deletions": 2,
        "title": "Fix off-by-one in counter",
        "body": "Routine fix",
        "filesMetadataValid": True,
        "labelsMetadataValid": True,
    }
    view.update(overrides)
    return view


class TestProjectRiskConfig(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper_module()
        self.tmp_obj = tempfile.TemporaryDirectory(prefix="aopr-risk-")
        self.root = Path(self.tmp_obj.name)
        (self.root / ".qwen").mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-b", "main"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "risk-test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Risk Test"], check=True)
        (self.root / "README.md").write_text("# risk test\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "baseline"], check=True, capture_output=True)

    def tearDown(self):
        self.tmp_obj.cleanup()

    def write(self, obj, track=True):
        path = self.root / ".qwen" / "review-config.json"
        path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
        if track:
            subprocess.run(["git", "-C", str(self.root), "add", ".qwen/review-config.json"], check=True)
            subprocess.run(["git", "-C", str(self.root), "commit", "-m", "risk config"], check=True, capture_output=True)
        return path

    def test_absent_config_uses_empty_extension(self):
        cfg, meta = self.mod.load_project_risk_config(self.root)
        self.assertEqual(cfg, {"highRiskPaths": (), "highRiskLabels": ()})
        self.assertFalse(meta["present"])
        self.assertIsNone(meta["sha256"])

    def test_valid_config_is_normalized_and_hashed(self):
        self.write({
            "schemaVersion": 1,
            "highRiskPaths": ["src/security/**", "package-lock.json"],
            "highRiskLabels": ["Data-Migration"],
        })
        cfg, meta = self.mod.load_project_risk_config(self.root)
        self.assertEqual(cfg["highRiskPaths"], ("src/security/**", "package-lock.json"))
        self.assertEqual(cfg["highRiskLabels"], ("data-migration",))
        self.assertTrue(meta["present"])
        self.assertRegex(meta["sha256"], r"^[0-9a-f]{64}$")

    def test_unknown_key_fails_closed(self):
        self.write({"schemaVersion": 1, "highRiskPaths": [], "disableGlobal": True})
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            self.mod.load_project_risk_config(self.root)

    def test_parent_traversal_path_fails_closed(self):
        self.write({"schemaVersion": 1, "highRiskPaths": ["../secrets/**"]})
        with self.assertRaisesRegex(ValueError, "repository-relative POSIX"):
            self.mod.load_project_risk_config(self.root)

    def test_wrong_schema_version_fails_closed(self):
        self.write({"schemaVersion": 2, "highRiskPaths": []})
        with self.assertRaisesRegex(ValueError, "schemaVersion must be exactly 1"):
            self.mod.load_project_risk_config(self.root)

    def test_symlink_fails_closed(self):
        target = self.root / "outside.json"
        target.write_text('{"schemaVersion": 1}\n', encoding="utf-8")
        path = self.root / ".qwen" / "review-config.json"
        path.symlink_to(target)
        subprocess.run(["git", "-C", str(self.root), "add", ".qwen/review-config.json"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "symlink config"], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "tracked regular file"):
            self.mod.load_project_risk_config(self.root)

    def test_untracked_config_fails_closed(self):
        self.write({"schemaVersion": 1, "highRiskPaths": []}, track=False)
        with self.assertRaisesRegex(ValueError, "untracked .* is not trusted policy"):
            self.mod.load_project_risk_config(self.root)

    def test_case_insensitive_duplicate_labels_fail_closed(self):
        self.write({
            "schemaVersion": 1,
            "highRiskLabels": ["Data-Migration", "data-migration"],
        })
        with self.assertRaisesRegex(ValueError, "unique case-insensitively"):
            self.mod.load_project_risk_config(self.root)


class TestEffortPolicyUnit(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper_module()
        self.plain_patch = "--- a/internal/app/app.go\n+++ b/internal/app/app.go\n"
        self.project_risk = {
            "highRiskPaths": (
                "go.mod", "go.sum", "web/package.json", "web/package-lock.json",
                "Dockerfile*", ".goreleaser*", "pkg/**", "internal/config/**",
            ),
            "highRiskLabels": (),
        }

    def select(self, requested, view, patch=None, patch_error=None):
        return self.mod.select_effort(
            requested, view, self.plain_patch if patch is None else patch,
            patch_error, self.project_risk,
        )

    def test_routine_selects_medium(self):
        effort, reasons = self.select("auto", routine_view())
        self.assertEqual(effort, "medium")
        self.assertEqual(reasons, ["no high-risk rules matched (policy v2)"])

    def test_concurrency_change_selects_high(self):
        patch = "+ var mu sync.Mutex\n+ func work() { go func() { ch <- v }() }\n"
        effort, reasons = self.select("auto", routine_view(), patch=patch)
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("patch risk term: concurrency") for r in reasons)
        )

    def test_security_auth_change_selects_high(self):
        patch = "+ token := readToken()\n+ if !auth(token) { deny() }\n"
        effort, reasons = self.select("auto", routine_view(), patch=patch)
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("patch risk term: authentication/permissions")
                for r in reasons)
        )

    def test_pkg_public_api_change_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(filePaths=["pkg/api/types.go"])
        )
        self.assertEqual(effort, "high")
        self.assertIn("high-risk path: pkg/** (matched 'pkg/api/types.go')", reasons)

    def test_contract_change_selects_high(self):
        effort, _ = self.select("auto", routine_view(filePaths=["PROJECT.md"]))
        self.assertEqual(effort, "high")

    def test_architecture_change_selects_high(self):
        effort, _ = self.select("auto", routine_view(filePaths=["ARCHITECTURE.md"]))
        self.assertEqual(effort, "high")

    def test_qwen_rules_change_selects_high(self):
        effort, _ = self.select(
            "auto", routine_view(filePaths=[".qwen/review-rules.md"])
        )
        self.assertEqual(effort, "high")

    def test_verify_script_change_selects_high(self):
        effort, _ = self.select("auto", routine_view(filePaths=["scripts/verify"]))
        self.assertEqual(effort, "high")

    def test_ci_workflow_change_selects_high(self):
        effort, _ = self.select(
            "auto", routine_view(filePaths=[".github/workflows/verify.yml"])
        )
        self.assertEqual(effort, "high")

    def test_dependency_change_selects_high(self):
        effort, _ = self.select("auto", routine_view(filePaths=["go.mod"]))
        self.assertEqual(effort, "high")

    def test_toolchain_lockfile_change_selects_high(self):
        effort, _ = self.select("auto", routine_view(filePaths=["go.sum"]))
        self.assertEqual(effort, "high")

    def test_build_release_change_selects_high(self):
        effort, _ = self.select(
            "auto",
            routine_view(filePaths=["Dockerfile.prod", ".goreleaser.yaml"]),
        )
        self.assertEqual(effort, "high")

    def test_web_dependency_change_selects_high(self):
        effort, _ = self.select(
            "auto", routine_view(filePaths=["web/package.json"])
        )
        self.assertEqual(effort, "high")

    def test_runtime_config_change_selects_high(self):
        effort, _ = self.select(
            "auto", routine_view(filePaths=["internal/config/config.go"])
        )
        self.assertEqual(effort, "high")

    def test_large_change_by_files_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(changedFiles=15)
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("large change: changedFiles=15") for r in reasons)
        )

    def test_large_change_by_lines_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(additions=300, deletions=200)
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("large change: additions+deletions=500") for r in reasons)
        )

    def test_high_risk_label_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(labels=["Risk:High"])
        )
        self.assertEqual(effort, "high")
        self.assertIn("high-risk label: risk:high", reasons)

    def test_title_body_marker_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(title="security: rotate token storage")
        )
        self.assertEqual(effort, "high")
        self.assertTrue(any("title/body risk marker" in r for r in reasons))

    def test_incomplete_diff_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(), patch=None,
            patch_error="patch retrieval failed (gh pr diff exit 1)",
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("incomplete risk metadata") for r in reasons)
        )

    def test_patch_over_bound_selects_high(self):
        effort, reasons = self.select(
            "auto", routine_view(), patch=None,
            patch_error="patch exceeds safe inspection bound (524288 bytes)",
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any("exceeds safe inspection bound" in r for r in reasons)
        )

    def test_missing_size_fields_selects_high(self):
        effort, _ = self.select(
            "auto", routine_view(changedFiles=None, additions=None, deletions=None)
        )
        self.assertEqual(effort, "high")

    def test_thirty_file_list_no_longer_assumed_truncated(self):
        view = routine_view(
            filePaths=[f"internal/app/f{i:02d}.go" for i in range(30)],
            changedFiles=30,
        )
        effort, reasons = self.select("auto", view)
        # high only because 30 >= 15 changed files, never because of a
        # 30-entry page-size assumption
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(r.startswith("large change: changedFiles=30") for r in reasons)
        )
        self.assertFalse(
            any("truncated" in r for r in reasons),
            "the 30-entry page-size assumption must be gone",
        )

    def test_explicit_high_remains_high(self):
        effort, reasons = self.select("high", routine_view())
        self.assertEqual(effort, "high")
        self.assertEqual(reasons[0], "explicit high effort requested")

    def test_explicit_medium_promoted_when_high_risk(self):
        effort, reasons = self.select(
            "medium", routine_view(filePaths=["pkg/api/types.go"])
        )
        self.assertEqual(effort, "high")
        self.assertEqual(reasons[0], "explicit medium promoted to high")
        self.assertIn("high-risk path: pkg/** (matched 'pkg/api/types.go')", reasons)

    def test_explicit_medium_routine_remains_medium(self):
        effort, _ = self.select("medium", routine_view())
        self.assertEqual(effort, "medium")

    def test_selector_is_deterministic(self):
        view = routine_view(filePaths=["go.mod"], labels=["Security"])
        first = self.select("auto", view)
        second = self.select("auto", view)
        self.assertEqual(first, second)


class TestRealGhMetadata(unittest.TestCase):
    """The policy adapter must consume real gh 2.98.0 flattened
    `files`/`labels` arrays and fail closed on anything malformed."""

    def setUp(self):
        self.mod = load_helper_module()
        self.plain_patch = "--- a/internal/app/app.go\n+++ b/internal/app/app.go\n"
        self.project_risk = {
            "highRiskPaths": ("go.mod",),
            "highRiskLabels": (),
        }

    def pr_doc(self, files=None, labels=None, changed_files=1):
        doc = {
            "changedFiles": changed_files,
            "additions": 5,
            "deletions": 2,
            "title": "Fix off-by-one in counter",
            "body": "Routine fix",
        }
        if files is not None:
            doc["files"] = files
        if labels is not None:
            doc["labels"] = labels
        return doc

    def select(self, requested, doc):
        return self.mod.select_effort(
            requested, self.mod.policy_view(doc), self.plain_patch, None,
            self.project_risk,
        )

    def test_policy_view_extracts_real_arrays(self):
        view = self.mod.policy_view(
            self.pr_doc(
                files=[{"path": "go.mod"}, {"path": "internal/app/app.go"}],
                labels=[{"name": "security"}],
                changed_files=2,
            )
        )
        self.assertEqual(view["filePaths"], ["go.mod", "internal/app/app.go"])
        self.assertEqual(view["labels"], ["security"])
        self.assertTrue(view["filesMetadataValid"])
        self.assertTrue(view["labelsMetadataValid"])

    def test_go_mod_in_real_files_array_selects_high(self):
        effort, reasons = self.select(
            "auto",
            self.pr_doc(
                files=[{"path": "internal/app/app.go"}, {"path": "go.mod"}],
                changed_files=2,
            ),
        )
        self.assertEqual(effort, "high")
        self.assertIn("high-risk path: go.mod (matched 'go.mod')", reasons)

    def test_security_label_in_real_labels_array_selects_high(self):
        effort, reasons = self.select(
            "auto",
            self.pr_doc(
                files=[{"path": "internal/app/app.go"}],
                labels=[{"name": "Security"}],
            ),
        )
        self.assertEqual(effort, "high")
        self.assertIn("high-risk label: security", reasons)

    def test_routine_valid_arrays_select_medium(self):
        effort, _ = self.select(
            "auto",
            self.pr_doc(
                files=[{"path": "internal/app/app.go"}],
                labels=[],
            ),
        )
        self.assertEqual(effort, "medium")

    def test_missing_files_metadata_selects_high(self):
        effort, reasons = self.select(
            "auto",
            self.pr_doc(labels=[{"name": "bug"}]),
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any(
                "files metadata missing, wrong-type, or malformed" in r
                for r in reasons
            )
        )

    def test_wrong_type_files_metadata_selects_high(self):
        effort, reasons = self.select(
            "auto", self.pr_doc(files="internal/app/app.go")
        )
        self.assertEqual(effort, "high")
        self.assertTrue(any("files metadata" in r for r in reasons))

    def test_malformed_files_entry_selects_high_and_keeps_valid_paths(self):
        doc = self.pr_doc(
            files=[{"path": "go.mod"}, "not-an-object", {"path": 42}],
            changed_files=1,
        )
        view = self.mod.policy_view(doc)
        self.assertEqual(view["filePaths"], ["go.mod"])
        self.assertFalse(view["filesMetadataValid"])
        effort, reasons = self.select("auto", doc)
        self.assertEqual(effort, "high")
        self.assertTrue(any("files metadata" in r for r in reasons))

    def test_malformed_labels_entry_selects_high(self):
        effort, reasons = self.select(
            "auto",
            self.pr_doc(
                files=[{"path": "internal/app/app.go"}],
                labels=[42],
            ),
        )
        self.assertEqual(effort, "high")
        self.assertTrue(any("labels metadata" in r for r in reasons))

    def test_changed_files_exceeding_valid_paths_selects_high(self):
        effort, reasons = self.select(
            "auto",
            self.pr_doc(
                files=[{"path": "internal/app/app.go"}],
                labels=[],
                changed_files=5,
            ),
        )
        self.assertEqual(effort, "high")
        self.assertTrue(
            any("exceeds the 1 valid file paths returned" in r for r in reasons)
        )

    def test_explicit_medium_promoted_for_high_risk_arrays(self):
        effort, reasons = self.select(
            "medium", self.pr_doc(files=[{"path": "go.mod"}])
        )
        self.assertEqual(effort, "high")
        self.assertEqual(reasons[0], "explicit medium promoted to high")


class TestEffortPolicyEndToEnd(HelperBase):
    def test_routine_selected_medium_in_command(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = self.qwen_review_argv()
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")

    def test_high_risk_selected_high_in_command(self):
        pr = self.default_pr_doc()
        pr["files"] = [{"path": "pkg/api/types.go"}]
        pr["changedFiles"] = 1
        self.setup_success(
            pr=pr, companion=make_companion(effort="high")
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = self.qwen_review_argv()
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_explicit_medium_promoted_and_recorded(self):
        pr = self.default_pr_doc()
        pr["files"] = [{"path": "pkg/api/types.go"}]
        pr["changedFiles"] = 1
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA, "medium")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "medium")
        self.assertEqual(doc["selectedEffort"], "high")
        self.assertEqual(doc["effortReasons"][0], "explicit medium promoted to high")
        self.assertIn("high-risk path: pkg/** (matched 'pkg/api/types.go')",
                      doc["effortReasons"])

    def test_explicit_high_on_routine_pr(self):
        self.setup_success(companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA, "high")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "high")
        self.assertEqual(doc["selectedEffort"], "high")
        self.assertEqual(doc["effortReasons"][0], "explicit high effort requested")

    def test_patch_retrieval_failure_selects_high(self):
        self.setup_success(
            gh_kw={"diff_fail": True}, companion=make_companion(effort="high")
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["selectedEffort"], "high")
        self.assertTrue(
            any("incomplete risk metadata" in r for r in doc["effortReasons"])
        )

    def test_effort_reasons_and_version_persisted(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["effortPolicyVersion"], 2)
        self.assertIsInstance(doc["effortReasons"], list)
        self.assertTrue(doc["effortReasons"])

    def test_real_gh_files_array_go_mod_selects_high(self):
        pr = self.default_pr_doc()
        pr["files"] = [{"path": "go.mod"}]
        pr["changedFiles"] = 1
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        argv = self.qwen_review_argv()
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        doc = self.read_result()
        self.assertIn(
            "high-risk path: go.mod (matched 'go.mod')", doc["effortReasons"]
        )

    def test_real_gh_labels_array_security_selects_high(self):
        pr = self.default_pr_doc()
        pr["files"] = [{"path": "internal/app/app.go"}]
        pr["changedFiles"] = 1
        pr["labels"] = [{"name": "Security"}]
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        argv = self.qwen_review_argv()
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        doc = self.read_result()
        self.assertIn("high-risk label: security", doc["effortReasons"])

    def test_invalid_project_risk_config_fails_before_qwen(self):
        self.setup_success()
        (self.repo / ".qwen" / "review-config.json").write_text(
            '{"schemaVersion": 1, "disableGlobal": true}\n', encoding="utf-8"
        )
        self.git("add", ".qwen/review-config.json")
        self.git("commit", "-m", "invalid risk policy fixture")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("invalid project review risk configuration", result.stdout + result.stderr)
        self.assertEqual(self.invocations("qwen"), [])

    def test_malformed_files_metadata_selects_high_end_to_end(self):
        pr = self.default_pr_doc()
        pr["files"] = "internal/app/app.go"  # wrong type for real gh arrays
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["selectedEffort"], "high")
        self.assertTrue(
            any("files metadata" in r for r in doc["effortReasons"])
        )

    def test_missing_labels_metadata_selects_high_end_to_end(self):
        pr = self.default_pr_doc()
        del pr["labels"]
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["selectedEffort"], "high")
        self.assertTrue(
            any("labels metadata" in r for r in doc["effortReasons"])
        )


# ---------------------------------------------------------------------------
# Native command and Qwen 0.22.3 output validation
# ---------------------------------------------------------------------------


class TestNativeCommand(HelperBase):
    def test_medium_command_construction(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            self.qwen_review_argv(),
            [
                "review", "run", "https://github.com/acme/demo/pull/5",
                "--effort", "medium",
                "--json",
                "--fail-on", "request-changes",
                "--approval-mode", "yolo",
                "--timeout-minutes", "240",
            ],
        )

    def test_high_command_construction(self):
        self.setup_success(companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA, "high")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = self.qwen_review_argv()
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_no_comment_or_resume_flags(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for entry in self.invocation_log():
            self.assertNotIn("--comment", entry["argv"])
            self.assertNotIn("--resume", entry["argv"])

    def test_exactly_one_semantic_review_and_one_version_call(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(self.invocations("qwen", "review", "run")), 1)
        self.assertEqual(self.invocations("qwen", "--version"), [["--version"]])

    def test_native_exit_1_is_review_error_with_artifacts(self):
        self.setup_success(
            companion=make_companion(event="APPROVE", base_event="APPROVE"),
            qwen_exit=1,
            qwen_stderr="boom\n",
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("qwen exit 1", doc["error"])
        run_dir = self.run_dirs()[-1]
        self.assertTrue((run_dir / "qwen-run.json").is_file())
        stderr_log = (run_dir / "qwen-stderr.log").read_text(encoding="utf-8")
        self.assertIn("boom", stderr_log)

    def test_exit_3_parsed_completely_with_findings_preserved(self):
        findings = [
            {"id": "R1", "severity": "Suggestion", "confidence": "high",
             "summary": "s1", "failureScenario": "f1"},
            {"id": "R2", "severity": "Nice to have", "confidence": "low",
             "summary": "s2", "failureScenario": "f2"},
        ]
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="REQUEST_CHANGES",
                base_event="REQUEST_CHANGES",
            ),
            companion=make_companion(
                event="REQUEST_CHANGES", base_event="REQUEST_CHANGES",
                findings=findings,
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "blocked")
        self.assertEqual(doc["qwenExitCode"], 3)
        self.assertEqual(doc["completed"], True)
        self.assertEqual(doc["timedOut"], False)
        self.assertEqual(doc["event"], "REQUEST_CHANGES")
        self.assertEqual([f["id"] for f in doc["findings"]], ["R1", "R2"])

    def test_missing_wrapper_is_review_error(self):
        self.set_qwen(wrapper_stdout="no json here at all")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("no wrapper JSON object", doc["error"])
        run_dir = self.run_dirs()[-1]
        # raw stdout preserved even though validation failed
        self.assertEqual(
            (run_dir / "qwen-run.json").read_text(encoding="utf-8"),
            "no json here at all",
        )

    def test_wrapper_not_object_is_review_error(self):
        self.set_qwen(wrapper_stdout="[1, 2, 3]")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.read_result()["disposition"], "review_error")

    def test_completed_false_is_review_error(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"), completed=False
            ),
            companion=make_companion(),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("completed is not true", doc["error"])

    def test_timed_out_true_is_review_error(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"), timed_out=True
            ),
            companion=make_companion(),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("timedOut is not false", doc["error"])

    def test_missing_report_file_is_review_error(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "absent.md")
            ),
            companion=make_companion(),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("reportPath file not found", doc["error"])

    def test_missing_companion_is_review_error(self):
        # valid wrapper, but the fake qwen writes only the .md report
        self.set_qwen(
            wrapper_stdout=json.dumps(
                make_wrapper(report_path=str(self.report_dir / "report.md"))
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("companion file not found", doc["error"])

    def test_malformed_companion_is_review_error(self):
        # companion file exists but is unparseable garbage
        self.set_qwen(
            wrapper_stdout=json.dumps(
                make_wrapper(report_path=str(self.report_dir / "report.md"))
            ),
            report_json_raw="{ not valid json",
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("not valid JSON", doc["error"])

    def test_schema_version_2_rejected(self):
        self.setup_success(
            companion=make_companion(schema_version=2),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("schemaVersion is not exactly 1", doc["error"])

    def test_schema_version_bool_rejected(self):
        self.setup_success(companion=make_companion(schema_version=True))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("schemaVersion is not exactly 1", doc["error"])

    def test_wrong_target_rejected(self):
        self.setup_success(companion=make_companion(target="pr-999"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("target is not 'pr-5'", doc["error"])

    def test_wrong_effort_rejected(self):
        self.setup_success(companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA)  # routine selects medium
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("does not match selected effort", doc["error"])

    def test_wrapper_companion_event_mismatch_rejected(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="COMMENT",
            ),
            companion=make_companion(event="APPROVE"),
            qwen_exit=0,
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("event differs", doc["error"])

    def test_wrapper_companion_base_event_mismatch_rejected(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                base_event="APPROVE",
            ),
            companion=make_companion(base_event="COMMENT"),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("baseEvent differs", doc["error"])

    def test_wrapper_companion_capped_by_mismatch_rejected(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                capped_by=["budget"],
            ),
            companion=make_companion(capped_by=[]),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("cappedBy differs", doc["error"])

    def test_exit_zero_with_request_changes_rejected(self):
        self.setup_success(
            companion=make_companion(event="REQUEST_CHANGES"),
            qwen_exit=0,
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="REQUEST_CHANGES",
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("expected 3", doc["error"])

    def test_exit_three_with_approve_rejected(self):
        self.setup_success(
            companion=make_companion(event="APPROVE", base_event="APPROVE"),
            qwen_exit=3,
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="APPROVE",
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("expected REQUEST_CHANGES", doc["error"])


# ---------------------------------------------------------------------------
# Actual Qwen vocabulary and disposition
# ---------------------------------------------------------------------------


class TestDisposition(HelperBase):
    def test_official_fixture_low_confidence_critical_needs_human(self):
        """The exact official-shaped fixture: title-case low-confidence
        Critical becomes needs_human, never pass."""
        companion = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(companion["target"], "pr-5")
        self.assertEqual(companion["effort"], "medium")
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="COMMENT",
                base_event="APPROVE",
            ),
            companion=companion,
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertNotEqual(result.returncode, 0, "must never be pass")
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "needs_human")
        finding = doc["findings"][0]
        self.assertEqual(finding["severity"], "Critical")
        self.assertEqual(finding["confidence"], "low")

    def test_high_confidence_critical_blocked(self):
        self.setup_success(
            companion=make_companion(
                event="COMMENT", base_event="APPROVE",
                findings=[
                    {
                        "id": "R1",
                        "severity": "Critical",
                        "confidence": "high",
                        "summary": "Broken mutex release",
                        "failureScenario": "Deadlock under load",
                    }
                ],
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "blocked")

    def test_suggestions_only_pass(self):
        self.setup_success(
            companion=make_companion(
                event="COMMENT", base_event="APPROVE",
                findings=[
                    {
                        "id": "R1",
                        "severity": "Suggestion",
                        "confidence": "high",
                        "summary": "MUST rename variable",
                        "failureScenario": "Readability",
                    },
                    {
                        "id": "R2",
                        "severity": "Nice to have",
                        "confidence": "low",
                        "summary": "Comment typo",
                        "failureScenario": "None",
                    },
                ],
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "pass")
        # preserved and available for display, never promoted to blockers
        self.assertEqual(
            [f["id"] for f in doc["findings"]], ["R1", "R2"]
        )
        self.assertEqual(doc["findings"][0]["severity"], "Suggestion")

    def test_lowercase_severity_fails_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "critical",
                        "confidence": "high",
                        "summary": "s",
                        "failureScenario": "f",
                    }
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("severity", doc["error"])

    def test_uppercase_severity_fails_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "CRITICAL",
                        "confidence": "high",
                        "summary": "s",
                        "failureScenario": "f",
                    }
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)

    def test_unknown_confidence_fails_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "Suggestion",
                        "confidence": "medium",
                        "summary": "s",
                        "failureScenario": "f",
                    }
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("confidence", doc["error"])

    def test_finding_missing_severity_fails_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "confidence": "high",
                        "summary": "s",
                        "failureScenario": "f",
                    }
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)

    def test_unknown_finding_fields_retained_verbatim(self):
        finding = {
            "id": "R1",
            "severity": "Suggestion",
            "confidence": "high",
            "summary": "keep everything",
            "failureScenario": "none",
            "file": "internal/app/app.go",
            "line": 42,
            "note": "extra field must survive",
        }
        self.setup_success(companion=make_companion(findings=[finding]))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        persisted = doc["findings"][0]
        for key, value in finding.items():
            self.assertEqual(persisted.get(key), value, key)

    def test_resolved_critical_outcomes_are_not_blockers(self):
        self.setup_success(
            companion=make_companion(
                event="COMMENT", base_event="APPROVE",
                findings=[
                    {
                        "id": "R1",
                        "severity": "Critical",
                        "confidence": "high",
                        "summary": "fixed issue",
                        "failureScenario": "none now",
                        "outcome": "fixed",
                    },
                    {
                        "id": "R2",
                        "severity": "Suggestion",
                        "confidence": "high",
                        "summary": "not needed",
                        "failureScenario": "none",
                        "outcome": "no_change_needed",
                    },
                ],
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "pass")
        self.assertEqual(doc["findings"][0]["outcome"], "fixed")

    def test_skipped_critical_remains_unresolved(self):
        self.setup_success(
            companion=make_companion(
                event="COMMENT", base_event="APPROVE",
                findings=[
                    {
                        "id": "R1",
                        "severity": "Critical",
                        "confidence": "high",
                        "summary": "skipped blocker",
                        "failureScenario": "still possible",
                        "outcome": "skipped",
                    }
                ],
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "blocked")

    def test_partial_outcomes_fail_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "Suggestion",
                        "confidence": "high",
                        "summary": "s1",
                        "failureScenario": "f1",
                        "outcome": "fixed",
                    },
                    {
                        "id": "R2",
                        "severity": "Suggestion",
                        "confidence": "low",
                        "summary": "s2",
                        "failureScenario": "f2",
                    },
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("partially populated", doc["error"])

    def test_unknown_outcome_fails_closed(self):
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "Suggestion",
                        "confidence": "high",
                        "summary": "s",
                        "failureScenario": "f",
                        "outcome": "wontfix",
                    }
                ]
            )
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        doc = self.read_result()
        self.assertIn("outcome", doc["error"])

    def test_downgraded_request_changes_needs_human(self):
        self.setup_success(
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="COMMENT",
                base_event="REQUEST_CHANGES",
            ),
            companion=make_companion(
                event="COMMENT", base_event="REQUEST_CHANGES"
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "needs_human")
        self.assertIn("downgraded", doc["nextAction"])

    def test_approve_pass(self):
        self.setup_success(
            companion=make_companion(event="APPROVE", base_event="APPROVE"),
            wrapper=make_wrapper(
                report_path=str(self.report_dir / "report.md"),
                event="APPROVE",
            ),
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "pass")
        self.assertEqual(doc["event"], "APPROVE")
        self.assertEqual(doc["baseEvent"], "APPROVE")

    def test_comment_base_approve_no_findings_pass(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.read_result()["disposition"], "pass")

    # -- exact-head and evidence safety ------------------------------------

    def test_post_review_head_drift_is_stale(self):
        # reads: resolve, early pre, final pre (immediately before launch),
        # post-review
        self.setup_success(
            gh_kw={"head_sequence": [
                VALID_SHA, VALID_SHA, VALID_SHA, DRIFT_SHA
            ]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "stale")
        self.assertEqual(doc["expectedHead"], VALID_SHA)
        self.assertEqual(doc["observedHeadAfter"], DRIFT_SHA)
        # the old verdict is preserved but explicitly invalidated
        self.assertIn("invalidated", doc["nextAction"])

    def test_post_review_gh_failure_is_review_error(self):
        self.setup_success(
            gh_kw={
                "head_sequence": [VALID_SHA, VALID_SHA, VALID_SHA, VALID_SHA],
                "head_read_failures": [False, False, False, True],
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIsNone(doc["observedHeadAfter"])
        self.assertIn("post-review head", doc["error"])

    def test_null_post_review_head_is_review_error(self):
        self.setup_success(
            gh_kw={"head_sequence": [VALID_SHA, VALID_SHA, VALID_SHA, None]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIsNone(doc["observedHeadAfter"])

    def test_head_moved_after_preparation_refuses_before_qwen(self):
        # reads: resolve, early pre (both VALID), final pre (DRIFT) — the
        # head moved during patch inspection/preparation, so the final
        # pre-review read must refuse before the single qwen review run.
        self.setup_success(
            gh_kw={"head_sequence": [VALID_SHA, VALID_SHA, DRIFT_SHA]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("head moved after preparation", doc["error"])
        self.assertEqual(doc["observedHeadBefore"], DRIFT_SHA)
        self.assertIsNone(doc["observedHeadAfter"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])
        # qwen --version precedes the final head check and is allowed
        self.assertEqual(self.invocations("qwen", "--version"), [["--version"]])

    def test_head_read_failure_in_final_pre_review_read_refuses(self):
        # resolve, early pre ok; final pre read fails -> refuse before Qwen
        self.setup_success(
            gh_kw={
                "head_sequence": [VALID_SHA, VALID_SHA, VALID_SHA],
                "head_read_failures": [False, False, True],
            }
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("final pre-review head read", doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_three_heads_remain_distinct_fields(self):
        self.setup_success(
            gh_kw={"head_sequence": [
                VALID_SHA, VALID_SHA, VALID_SHA, DRIFT_SHA
            ]}
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 4)
        doc = self.read_result()
        self.assertEqual(doc["observedHeadAtResolve"], VALID_SHA)
        self.assertEqual(doc["observedHeadBefore"], VALID_SHA)
        self.assertEqual(doc["observedHeadAfter"], DRIFT_SHA)


# ---------------------------------------------------------------------------
# Wrapper sourcing: current stdout only, never a stale repository file
# ---------------------------------------------------------------------------


class TestWrapperSourceIsolation(HelperBase):
    def _plant_stale_root_wrapper(self):
        stale = make_wrapper(report_path=str(self.report_dir / "report.md"))
        (self.repo / "qwen-run.json").write_text(json.dumps(stale))

    def test_empty_stdout_with_stale_root_qwen_run_json_is_review_error(self):
        self._plant_stale_root_wrapper()
        self.set_qwen(wrapper_stdout="")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("no wrapper JSON object", doc["error"])
        # the native run itself happened once; the stale file was not used
        self.assertEqual(len(self.invocations("qwen", "review", "run")), 1)
        # raw (empty) stdout is preserved verbatim
        run_dir = self.run_dirs()[-1]
        self.assertEqual((run_dir / "qwen-run.json").read_text("utf-8"), "")

    def test_invalid_stdout_with_stale_root_qwen_run_json_is_review_error(self):
        self._plant_stale_root_wrapper()
        self.set_qwen(wrapper_stdout="this is not json at all\n")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("no wrapper JSON object", doc["error"])
        self.assertNotIn("qwen-run.json file", doc["error"])


# ---------------------------------------------------------------------------
# Companion verdict strict validation
# ---------------------------------------------------------------------------


class TestCompanionVerdictValidation(HelperBase):
    def test_missing_companion_event_is_review_error(self):
        companion = make_companion()
        del companion["verdict"]["event"]
        self.setup_success(companion=companion)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("verdict.event", doc["error"])

    def test_invalid_companion_event_is_review_error(self):
        self.setup_success(companion=make_companion(event="MAYBE"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("verdict.event", doc["error"])
        self.assertIn("MAYBE", doc["error"])

    def test_lowercase_companion_event_is_review_error(self):
        self.setup_success(companion=make_companion(event="approve"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "review_error")

    def test_missing_companion_base_event_is_review_error(self):
        companion = make_companion()
        del companion["verdict"]["baseEvent"]
        self.setup_success(companion=companion)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("verdict.baseEvent", doc["error"])

    def test_invalid_companion_base_event_is_review_error(self):
        self.setup_success(companion=make_companion(base_event="MERGE"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("verdict.baseEvent", doc["error"])

    def test_missing_companion_capped_by_is_review_error(self):
        companion = make_companion()
        del companion["verdict"]["cappedBy"]
        self.setup_success(companion=companion)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("cappedBy is not an array of strings", doc["error"])

    def test_string_companion_capped_by_is_review_error(self):
        self.setup_success(companion=make_companion(capped_by="medium-effort"))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("cappedBy is not an array of strings", doc["error"])
        # medium-effort is never synthesized into the recorded value
        self.assertNotEqual(doc["cappedBy"], ["medium-effort"])

    def test_mixed_type_companion_capped_by_is_review_error(self):
        self.setup_success(companion=make_companion(capped_by=["budget", 3]))
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("cappedBy is not an array of strings", doc["error"])

    def test_malformed_verdict_cases_never_pass(self):
        cases = {
            "missing event": (lambda c: c["verdict"].pop("event")),
            "invalid event": lambda c: c["verdict"].__setitem__("event", "MAYBE"),
            "lowercase event": lambda c: c["verdict"].__setitem__("event", "approve"),
            "missing baseEvent": (lambda c: c["verdict"].pop("baseEvent")),
            "invalid baseEvent": (
                lambda c: c["verdict"].__setitem__("baseEvent", "MERGE")
            ),
            "missing cappedBy": (lambda c: c["verdict"].pop("cappedBy")),
            "string cappedBy": (
                lambda c: c["verdict"].__setitem__("cappedBy", "medium-effort")
            ),
            "mixed-type cappedBy": (
                lambda c: c["verdict"].__setitem__("cappedBy", ["budget", 3])
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                companion = make_companion()
                mutate(companion)
                self.setup_success(companion=companion)
                result = self.run_helper("5", VALID_SHA)
                self.assertNotEqual(result.returncode, 0, name)
                doc = self.read_result()
                self.assertEqual(doc["disposition"], "review_error", name)


# ---------------------------------------------------------------------------
# Repository guard: clean tracked/staged baseline required around Qwen
# ---------------------------------------------------------------------------


class TestDirtyBaselineGuard(HelperBase):
    def test_dirty_tracked_baseline_prevents_qwen(self):
        (self.repo / "app.go").write_text(
            "package main\n\nfunc main() { // pre-existing tracked change\n }\n"
        )
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("tracked/staged changes before", doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])
        self.assertEqual(self.invocations("qwen", "--version"), [])

    def test_staged_only_baseline_prevents_qwen(self):
        (self.repo / "README.md").write_text("# demo app\nstaged edit\n")
        self.git("add", "README.md")
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("tracked/staged changes before", doc["error"])
        self.assertEqual(self.invocations("qwen", "review", "run"), [])

    def test_untracked_baseline_artifact_is_still_allowed(self):
        (self.repo / "qwen-artifact.tmp").write_text("pre-existing untracked\n")
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "pass")


# ---------------------------------------------------------------------------
# Evidence and locking
# ---------------------------------------------------------------------------


class TestEvidenceAndLocking(HelperBase):
    def test_two_runs_same_second_get_different_dirs(self):
        self.setup_success()
        first = self.run_helper("5", VALID_SHA)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        second = self.run_helper("5", VALID_SHA)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        dirs = self.run_dirs()
        self.assertEqual(len(dirs), 2)
        self.assertNotEqual(dirs[0].name, dirs[1].name)
        for path in dirs:
            self.assertTrue((path / "result.json").is_file())

    def test_concurrent_lock_prevents_second_qwen_without_overwrite(self):
        self.setup_success()
        first = self.run_helper("5", VALID_SHA)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_dir = self.run_dirs()[0]
        sentinel = first_dir / "sentinel.txt"
        sentinel.write_text("do not touch\n", encoding="utf-8")
        first_result_hash = sha256_of(first_dir / "result.json")

        lock_path = (
            self.state_root / "locks" / "acme__demo" / "pr-5.lock"
        )
        self.assertTrue(lock_path.exists())
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            second = self.run_helper("5", VALID_SHA)
        finally:
            os.close(lock_fd)
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        # the failed run recorded its own diagnostic in its own directory
        self.assertEqual(len(self.run_dirs()), 2)
        second_dir = self.run_dirs()[1]
        second_doc = json.loads(
            (second_dir / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(second_doc["disposition"], "review_error")
        self.assertIn("lock", second_doc["error"])
        # Qwen was not launched a second time
        self.assertEqual(len(self.invocations("qwen", "review", "run")), 1)
        # the first run's evidence is untouched
        self.assertEqual(sha256_of(first_dir / "result.json"), first_result_hash)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch\n")

    def test_review_json_copied_into_run_dir(self):
        companion = make_companion()
        self.setup_success(companion=companion)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        run_dir = self.run_dirs()[-1]
        copied = json.loads((run_dir / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(copied, companion)
        self.assertTrue((run_dir / "review.md").is_file())

    def test_result_json_has_required_keys(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        required = [
            "contractVersion", "effortPolicyVersion", "repository",
            "prNumber", "prUrl", "reviewKey", "attemptId",
            "invocationTransport", "requestedEffort", "selectedEffort",
            "effortReasons", "expectedHead", "observedHeadAtResolve",
            "observedHeadBefore", "observedHeadAfter", "requiredChecks",
            "qwenVersion", "startedAt", "finishedAt", "qwenExitCode",
            "completed", "timedOut", "event", "baseEvent", "cappedBy",
            "localIdentityBefore", "localIdentityAfter",
            "findings", "disposition", "semanticExitCode", "nextAction",
            "artifacts", "artifactSha256",
        ]
        for key in required:
            self.assertIn(key, doc, f"missing result.json key: {key}")
        self.assertEqual(doc["contractVersion"], 6)
        self.assertEqual(doc["invocationTransport"], "direct")
        self.assertEqual(doc["semanticExitCode"], 0)
        self.assertEqual(doc["qwenVersion"], "qwen 0.22.3")

    def test_artifact_hashes_recorded(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        run_dir = self.run_dirs()[-1]
        self.assertEqual(
            doc["artifactSha256"]["review.md"],
            sha256_of(run_dir / "review.md"),
        )
        self.assertEqual(
            doc["artifactSha256"]["qwen-run.json"],
            sha256_of(run_dir / "qwen-run.json"),
        )
        for name in (
            "preflight.json", "qwen-run.json", "qwen-stderr.log",
            "review.md", "review.json", "result.json",
        ):
            self.assertIn(name, doc["artifacts"])
        self.assertTrue((run_dir / "preflight.json").is_file())
        preflight = json.loads(
            (run_dir / "preflight.json").read_text(encoding="utf-8")
        )
        self.assertEqual(preflight["prNumber"], 5)
        self.assertEqual(preflight["expectedHead"], VALID_SHA)

    def test_final_output_lines(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        self.assertEqual(lines[-3], "DISPOSITION=pass")
        self.assertEqual(lines[-2], "SEMANTIC_EXIT_CODE=0")
        self.assertTrue(lines[-1].startswith("RESULT_JSON="))
        self.assertTrue(Path(lines[-1].split("=", 1)[1]).is_file())

    def test_tracked_repository_mutation_is_review_error(self):
        self.setup_success(
            mutate_tracked_file=self.repo / "app.go",
            create_untracked_file=self.repo / "qwen-artifact.tmp",
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("tracked repository content changed", doc["error"])
        # nothing was repaired or reverted automatically
        self.assertIn(
            "simulated tracked mutation",
            (self.repo / "app.go").read_text(encoding="utf-8"),
        )

    def test_untracked_review_artifacts_are_allowed(self):
        self.setup_success(
            create_untracked_file=self.repo / "qwen-artifact.tmp"
        )
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.read_result()["disposition"], "pass")

    def test_atomic_result_write(self):
        module = load_helper_module()
        target = self.tmp / "atomic.json"
        module.atomic_write_json(target, {"a": 1, "b": [1, 2]})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                         {"a": 1, "b": [1, 2]})
        leftovers = [p for p in target.parent.glob(".atomic.json.*")]
        self.assertEqual(leftovers, [], "no temp files may be left behind")


# ---------------------------------------------------------------------------
# Forbidden operations (static verification of production code)
# ---------------------------------------------------------------------------


class TestForbiddenOperations(unittest.TestCase):
    def test_helper_source_contains_no_forbidden_behavior(self):
        source = HELPER.read_text(encoding="utf-8")
        forbidden_substrings = [
            "ao send",
            "set-config",
            "--comment",
            "--resume",
            "git commit",
            "git push",
            "git merge",
            "gh pr review",
            "gh pr merge",
            "gh api",
            "systemd",
            "cron",
            "Popen",
            "while True",
        ]
        for fragment in forbidden_substrings:
            self.assertNotIn(
                fragment, source,
                f"forbidden behavior marker found: {fragment!r}",
            )
        for word in ("poll", "timer"):
            self.assertIsNone(
                re.search(rf"\b{word}\w*\b", source),
                f"forbidden loop/scheduler marker found: {word!r}",
            )

    def test_git_usage_is_status_only(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn('"status"', source)
        self.assertIn('"rev-parse"', source)
        for token in ('"commit"', '"push"', '"merge"'):
            self.assertNotIn(token, source)

    def test_only_read_only_gh_commands_are_used(self):
        base = HelperBase()
        base.setUp()
        try:
            base.setup_success()
            result = base.run_helper("5", VALID_SHA)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            allowed_prefixes = (
                ("repo", "view"),
                ("pr", "view"),
                ("pr", "checks"),
                ("pr", "diff"),
            )
            for entry in base.invocation_log():
                if entry["exe"] == "gh":
                    self.assertIn(
                        tuple(entry["argv"][:2]),
                        allowed_prefixes,
                        f"unexpected gh command: {entry['argv']}",
                    )
                else:
                    self.assertTrue(
                        entry["argv"] == ["--version"]
                        or entry["argv"][:2] == ["review", "run"],
                        f"unexpected qwen command: {entry['argv']}",
                    )
        finally:
            base.tearDown()


# ---------------------------------------------------------------------------
# Skill documentation sanity
# ---------------------------------------------------------------------------


class TestSkillDocs(unittest.TestCase):
    def test_skill_frontmatter(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n", 2)[1]
        self.assertIn("name: ao-pr-review", frontmatter)
        self.assertIn(
            "description: Run one explicit, non-posting native Qwen semantic "
            "review of an exact GitHub PR head after required CI passes, "
            "selecting medium or high effort deterministically.",
            frontmatter,
        )
        self.assertNotIn("disable-model-invocation", frontmatter)

    def test_skill_injects_args_file_only(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<skill-args-file>", text)
        self.assertIn("--args-file <injected-path>", text)

    def test_readme_states_non_posting_language(self):
        readme = (SKILL_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "non-posting and makes no intended tracked application changes",
            readme,
        )

    def test_version_files(self):
        version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(version, "0.3.3")

    def test_fixture_shape(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schemaVersion"], 1)
        self.assertEqual(fixture["target"], "pr-5")
        self.assertEqual(fixture["effort"], "medium")
        self.assertEqual(fixture["verdict"]["event"], "COMMENT")
        self.assertEqual(fixture["verdict"]["baseEvent"], "APPROVE")
        self.assertIsInstance(fixture["verdict"]["verdictLine"], str)
        self.assertTrue(fixture["verdict"]["verdictLine"].strip())
        self.assertEqual(fixture["verdict"]["cappedBy"], [])
        self.assertEqual(fixture["findings"][0]["severity"], "Critical")
        self.assertEqual(fixture["findings"][0]["confidence"], "low")
        self.assertEqual(fixture["findings"][0]["source"], "review")
        self.assertTrue(fixture["findings"][0]["locations"])
        self.assertEqual(fixture["counts"]["total"], 1)
        self.assertTrue(
            fixture["markdownReportPath"].startswith(".qwen/reviews/")
        )
        self.assertTrue(fixture["markdownReportPath"].endswith(".md"))


# ---------------------------------------------------------------------------
# Result persistence must never false-pass
# ---------------------------------------------------------------------------


class TestResultPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="aopr-persist-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def ctx_with_unwritable_run_dir(self):
        # The run directory's parent path contains a regular file, so the
        # result.json persistence fails with an ordinary filesystem error
        # even though the run itself completed.
        blocker = self.tmp / "blocker"
        blocker.write_text("a directory is required under this path\n")
        run_dir = blocker / "runs" / "acme__demo" / "pr-5" / "run"
        return self.mod.new_ctx(
            run_dir,
            repository="acme/demo",
            prNumber=5,
            prUrl="https://github.com/acme/demo/pull/5",
            expectedHead=VALID_SHA,
        )

    def test_write_failure_never_emits_original_disposition(self):
        for disposition in ("pass", "blocked", "stale", "needs_human"):
            with self.subTest(disposition=disposition):
                ctx = self.ctx_with_unwritable_run_dir()
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = self.mod.finish(ctx, disposition)
                self.assertEqual(code, 3, disposition)
                stdout = buffer.getvalue()
                lines = [ln for ln in stdout.splitlines() if ln.strip()]
                self.assertEqual(
                    lines[-3:],
                    ["DISPOSITION=review_error", "SEMANTIC_EXIT_CODE=3",
                     "RESULT_JSON="],
                    disposition,
                )
                self.assertNotIn(
                    f"DISPOSITION={disposition}", stdout, disposition
                )
                self.assertIn("review_error", lines[0], disposition)
                self.assertIn(str(ctx["runDir"]), lines[0], disposition)
                self.assertNotIn("Traceback", stdout, disposition)


# ---------------------------------------------------------------------------
# Local repository identity guard (clean commits and ref changes)
# ---------------------------------------------------------------------------


class TestLocalIdentityGuard(HelperBase):
    def test_qwen_local_commit_is_review_error(self):
        # The fake Qwen mutates a tracked file and commits it, leaving a
        # clean git status — the status-only guard would miss this.
        before_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.setup_success(mutate_and_commit=self.repo / "app.go")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("local repository HEAD changed during review", doc["error"])
        after_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(before_head, after_head)
        # the worktree is clean again: only the HEAD comparison catches it
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        # nothing was reverted or repaired
        self.assertIn(
            "simulated tracked mutation with local commit",
            (self.repo / "app.go").read_text(encoding="utf-8"),
        )
        # before/after identity snapshots persisted in result.json
        self.assertEqual(doc["localIdentityBefore"]["head"], before_head)
        self.assertEqual(doc["localIdentityBefore"]["ref"], "main")
        self.assertEqual(doc["localIdentityBefore"]["trackedStatus"], [])
        self.assertEqual(doc["localIdentityAfter"]["head"], after_head)
        self.assertEqual(doc["localIdentityAfter"]["ref"], "main")

    def test_qwen_local_commit_next_action_refuses_repair(self):
        self.setup_success(mutate_and_commit=self.repo / "app.go")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertIn("automatic repair", doc["nextAction"])

    def test_branch_change_is_review_error(self):
        self.setup_success(switch_branch="work-branch")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("branch/ref changed during review", doc["error"])
        self.assertEqual(doc["localIdentityBefore"]["ref"], "main")
        self.assertEqual(doc["localIdentityAfter"]["ref"], "work-branch")

    def test_post_review_identity_read_failure_is_review_error(self):
        self.setup_success(delete_git_dir=True)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("identity read failed after the review", doc["error"])
        self.assertIsNone(doc["localIdentityAfter"])

    def test_identity_snapshots_persisted_on_success(self):
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["localIdentityBefore"]["head"], head)
        self.assertEqual(doc["localIdentityBefore"]["ref"], "main")
        self.assertEqual(doc["localIdentityBefore"]["trackedStatus"], [])
        self.assertEqual(doc["localIdentityAfter"], doc["localIdentityBefore"])


# ---------------------------------------------------------------------------
# Full Qwen 0.22.3 companion shape validation (fail-closed)
# ---------------------------------------------------------------------------


class TestCompanionShapeValidation(HelperBase):
    def fixture_companion(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def assert_review_error(self, companion):
        self.setup_success(companion=companion)
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        return doc

    def test_complete_fixture_passes_validation(self):
        module = load_helper_module()
        doc = self.fixture_companion()
        self.assertEqual(module.validate_companion(doc, 5, "medium"), [])
        self.assertEqual(module.validate_findings(doc["findings"]), [])

    def test_minimal_malformed_finding_is_review_error(self):
        # The exact shape that v0.2.1 incorrectly accepted (and could
        # return pass for).
        doc = self.fixture_companion()
        doc["findings"] = [{"severity": "Suggestion", "confidence": "high"}]
        result_doc = self.assert_review_error(doc)
        self.assertIn("finding[0]", result_doc["error"])

    def test_every_missing_or_malformed_required_field_fails_closed(self):
        cases = {
            # top level
            "missing counts": lambda d: d.pop("counts"),
            "non-object counts": lambda d: d.__setitem__("counts", "nope"),
            "missing total": (lambda d: d["counts"].pop("total")),
            "negative total": (
                lambda d: d["counts"].__setitem__("total", -1)
            ),
            "boolean total": (
                lambda d: d["counts"].__setitem__("total", True)
            ),
            "missing bySeverity": (
                lambda d: d["counts"].pop("bySeverity")
            ),
            "non-object bySeverity": (
                lambda d: d["counts"].__setitem__("bySeverity", "x")
            ),
            "missing bySeverity key": (
                lambda d: d["counts"]["bySeverity"].pop("Critical")
            ),
            "missing byConfidence": (
                lambda d: d["counts"].pop("byConfidence")
            ),
            "missing byConfidence key": (
                lambda d: d["counts"]["byConfidence"].pop("high")
            ),
            "missing held": (lambda d: d["counts"].pop("held")),
            "negative held": (
                lambda d: d["counts"].__setitem__("held", -2)
            ),
            "non-object byOutcome": (
                lambda d: d["counts"].__setitem__("byOutcome", "nope")
            ),
            "missing byOutcome key": (
                lambda d: d["counts"]["byOutcome"].pop("fixed")
            ),
            "missing markdownReportPath": (
                lambda d: d.pop("markdownReportPath")
            ),
            "absolute markdownReportPath": (
                lambda d: d.__setitem__(
                    "markdownReportPath", "/etc/reviews/report.md"
                )
            ),
            "traversal markdownReportPath": (
                lambda d: d.__setitem__(
                    "markdownReportPath", ".qwen/reviews/../report.md"
                )
            ),
            "non-md markdownReportPath": (
                lambda d: d.__setitem__(
                    "markdownReportPath", ".qwen/reviews/report.txt"
                )
            ),
            "outside reviews dir": (
                lambda d: d.__setitem__(
                    "markdownReportPath", ".qwen/notes/report.md"
                )
            ),
            # verdict
            "missing verdictLine": (
                lambda d: d["verdict"].pop("verdictLine")
            ),
            "empty verdictLine": (
                lambda d: d["verdict"].__setitem__("verdictLine", "")
            ),
            "whitespace verdictLine": (
                lambda d: d["verdict"].__setitem__("verdictLine", "   ")
            ),
            # findings
            "missing id": (lambda d: d["findings"][0].pop("id")),
            "empty id": (
                lambda d: d["findings"][0].__setitem__("id", "")
            ),
            "invalid severity": (
                lambda d: d["findings"][0].__setitem__("severity", "critical")
            ),
            "missing severity": (
                lambda d: d["findings"][0].pop("severity")
            ),
            "invalid confidence": (
                lambda d: d["findings"][0].__setitem__(
                    "confidence", "medium"
                )
            ),
            "missing source": (
                lambda d: d["findings"][0].pop("source")
            ),
            "invalid source": (
                lambda d: d["findings"][0].__setitem__("source", "scan")
            ),
            "missing summary": (
                lambda d: d["findings"][0].pop("summary")
            ),
            "missing shortSummary": (
                lambda d: d["findings"][0].pop("shortSummary")
            ),
            "empty failureScenario": (
                lambda d: d["findings"][0].__setitem__("failureScenario", "")
            ),
            "missing locations": (
                lambda d: d["findings"][0].pop("locations")
            ),
            "empty locations": (
                lambda d: d["findings"][0].__setitem__("locations", [])
            ),
            "non-array locations": (
                lambda d: d["findings"][0].__setitem__("locations", "nope")
            ),
            "non-object location": (
                lambda d: d["findings"][0].__setitem__(
                    "locations", ["not-an-object"]
                )
            ),
            "missing location file": (
                lambda d: d["findings"][0]["locations"][0].pop("file")
            ),
            "zero location line": (
                lambda d: d["findings"][0]["locations"][0].__setitem__(
                    "line", 0
                )
            ),
            "negative location line": (
                lambda d: d["findings"][0]["locations"][0].__setitem__(
                    "line", -3
                )
            ),
            "boolean location line": (
                lambda d: d["findings"][0]["locations"][0].__setitem__(
                    "line", True
                )
            ),
            "string location line": (
                lambda d: d["findings"][0]["locations"][0].__setitem__(
                    "line", "12"
                )
            ),
            "empty location anchor": (
                lambda d: d["findings"][0]["locations"][0].__setitem__(
                    "anchor", ""
                )
            ),
            # optional renderer fields, when present
            "empty witness": (
                lambda d: d["findings"][0].__setitem__("witness", "")
            ),
            "non-string suggestedFix": (
                lambda d: d["findings"][0].__setitem__("suggestedFix", 5)
            ),
            "null category": (
                lambda d: d["findings"][0].__setitem__("category", None)
            ),
            "empty outcomeNote": (
                lambda d: d["findings"][0].__setitem__("outcomeNote", "")
            ),
            "non-array assetFiles": (
                lambda d: d["findings"][0].__setitem__("assetFiles", "nope")
            ),
            "empty-string assets entry": (
                lambda d: d["findings"][0].__setitem__("assets", [""])
            ),
            "non-object heldByMeasurement": (
                lambda d: d["findings"][0].__setitem__(
                    "heldByMeasurement", "nope"
                )
            ),
            "empty heldByMeasurement file": (
                lambda d: d["findings"][0].__setitem__(
                    "heldByMeasurement", {"file": ""}
                )
            ),
            "invalid outcome": (
                lambda d: d["findings"][0].__setitem__("outcome", "wontfix")
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                doc = self.fixture_companion()
                mutate(doc)
                self.assert_review_error(doc)


# ---------------------------------------------------------------------------
# v0.2.3: effort-based native review timeout
# ---------------------------------------------------------------------------


class TestNativeTimeoutPolicy(unittest.TestCase):
    def test_medium_selects_240_minutes(self):
        module = load_helper_module()
        self.assertEqual(module.NATIVE_TIMEOUT_MINUTES["medium"], 240)

    def test_high_selects_480_minutes(self):
        module = load_helper_module()
        self.assertEqual(module.NATIVE_TIMEOUT_MINUTES["high"], 480)

    def test_medium_wrapper_timeout_is_native_plus_600_seconds(self):
        module = load_helper_module()
        minutes, wrapper_seconds = module.native_timeout_plan("medium")
        self.assertEqual(minutes, 240)
        self.assertEqual(wrapper_seconds, 240 * 60 + 600)

    def test_high_wrapper_timeout_is_native_plus_600_seconds(self):
        module = load_helper_module()
        minutes, wrapper_seconds = module.native_timeout_plan("high")
        self.assertEqual(minutes, 480)
        self.assertEqual(wrapper_seconds, 480 * 60 + 600)

    def test_wrapper_grace_is_600_seconds(self):
        module = load_helper_module()
        self.assertEqual(module.WRAPPER_CLEANUP_GRACE_SECONDS, 600)

    def test_python_timeout_uses_wrapper_timeout(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn("timeout=wrapper_timeout_seconds", source)
        self.assertIn("minutes * 60 + WRAPPER_CLEANUP_GRACE_SECONDS", source)


class TestNativeTimeoutEndToEnd(HelperBase):
    def assert_timeout_wiring(self, doc, flag_value):
        argv = self.qwen_review_argv()
        self.assertEqual(
            argv[argv.index("--timeout-minutes") + 1], str(flag_value)
        )
        self.assertEqual(doc["reviewTimeoutMinutes"], flag_value)
        self.assertEqual(doc["wrapperTimeoutSeconds"], flag_value * 60 + 600)

    def test_medium_run_passes_240_minutes(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["selectedEffort"], "medium")
        self.assert_timeout_wiring(doc, 240)

    def test_high_run_passes_480_minutes(self):
        self.setup_success(companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA, "high")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["selectedEffort"], "high")
        self.assert_timeout_wiring(doc, 480)

    def test_auto_uses_medium_timeout_when_medium_selected(self):
        self.setup_success()
        result = self.run_helper("5", VALID_SHA, "auto")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "auto")
        self.assertEqual(doc["selectedEffort"], "medium")
        self.assert_timeout_wiring(doc, 240)

    def test_auto_uses_high_timeout_when_high_selected(self):
        pr = self.default_pr_doc()
        pr["files"] = [{"path": "go.mod"}]
        pr["changedFiles"] = 1
        self.setup_success(pr=pr, companion=make_companion(effort="high"))
        result = self.run_helper("5", VALID_SHA, "auto")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["requestedEffort"], "auto")
        self.assertEqual(doc["selectedEffort"], "high")
        self.assert_timeout_wiring(doc, 480)


# ---------------------------------------------------------------------------
# v0.2.3: background execution contract (SKILL.md)
# ---------------------------------------------------------------------------


def _ao_skill_section(heading_prefix):
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    start = text.index(heading_prefix)
    end = text.index("\n## ", start + 1)
    return text[start:end]


class TestMonitorExecutionDocs(unittest.TestCase):
    """v0.2.5: real reviews launch through Qwen's native `monitor` tool."""

    def test_skill_mandates_native_monitor_tool(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn(
            "ONLY through Qwen's native `monitor`", section
        )
        self.assertIn("`monitor`\ntool.", section)
        self.assertIn("Do not use `run_shell_command`", section)
        self.assertNotIn("is_background: true", section)

    def test_background_shell_defect_documented(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn("settles silently", section)
        self.assertIn("/tasks", section)
        self.assertIn("does not push a completion notification", section)

    def test_launch_command_uses_monitor_envelope_args_file(self):
        section = _ao_skill_section("## Review execution")
        blocks = re.findall(r"```text\n(.*?)```", section, re.DOTALL)
        launch = [b for b in blocks if "run_explicit_review.py" in b]
        self.assertEqual(len(launch), 1)
        command = launch[0]
        self.assertIn("--monitor-envelope", command)
        self.assertIn("--args-file <injected-path>", command)
        self.assertNotIn("--background-envelope", command)
        self.assertNotIn("&", command)
        self.assertNotIn("nohup", command)
        self.assertNotIn("setsid", command)
        self.assertNotIn("disown", command)
        self.assertIsNone(
            re.search(r"(?<!idle_)timeout\s*[:=]", command),
            "no shell timeout parameter may be assigned to the launch",
        )

    def test_monitor_arguments_are_exact(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn("idle_timeout_ms:", section)
        self.assertIn("600000", section)
        self.assertIn("max_events:", section)
        self.assertIn("128", section)
        self.assertIn("the current repository worktree", section)

    def test_no_second_watcher_or_scheduler(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn("Do not add `&`, `nohup`", section)
        self.assertIn("a second watcher", section)
        self.assertIn("polling loop", section)
        self.assertIn("scheduler", section)
        self.assertIn("There is no shell timeout parameter", section)

    def test_launch_receipt_contract(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn("REVIEW STARTED", section)
        self.assertIn("the monitor ID", section)
        self.assertIn(
            "the dedicated AO reviewer session remains available", section
        )
        self.assertIn("end the model turn", section)
        self.assertIn(
            "Do not claim the PR or the expected head SHA at launch",
            section,
        )

    def test_session_termination_and_no_verdict(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn("can terminate", section)
        self.assertIn("no valid verdict", section)
        self.assertIn("re-run fresh", section)
        self.assertIn("exact current head SHA", section)

    def test_heartbeat_is_nonterminal(self):
        section = _ao_skill_section("## Handling Monitor events")
        self.assertIn("`heartbeat` — nonterminal", section)
        self.assertIn("REVIEW RUNNING", section)

    def test_complete_requires_validated_result(self):
        section = _ao_skill_section("## Handling Monitor events")
        self.assertIn("read and validate the exact `resultJson`", section)
        self.assertIn("display the full semantic result", section)

    def test_transport_failures_are_review_error(self):
        section = _ao_skill_section("## Handling Monitor events")
        for phrase in (
            "`transport_error`",
            "failed or cancelled monitor",
            "missing terminal event",
            "invalid or missing `result.json`",
            "`REVIEW ERROR`",
        ):
            self.assertIn(phrase, section)

    def test_completed_status_is_transport_only(self):
        section = _ao_skill_section("## Handling Monitor events")
        self.assertIn(
            "Never equate the monitor's `completed` status with a semantic PASS",
            section,
        )

    def test_skill_is_model_invocable(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---\n", 2)[1]
        self.assertNotIn("disable-model-invocation", frontmatter)
        self.assertNotIn("## Invocation (manual only)", text)
        self.assertIn("## Invocation", text)
        self.assertIn("exactly two supported invocation paths", text)
        self.assertIn("Dispatched AO reviewer", text)
        self.assertIn("exactly the dispatched arguments", text)


# ---------------------------------------------------------------------------
# v0.2.3: ownership documentation (README, SKILL, contract)
# ---------------------------------------------------------------------------


class TestOwnershipDocs(unittest.TestCase):
    DOCS = ("README.md", "SKILL.md", "references/contract.md")
    PHRASES = (
        "600000 ms",
        "/tasks",
        "completion notification",
        "Terminating the Qwen session can terminate",
        "no valid verdict",
        "re-run fresh",
        "exact current head SHA",
        "dedicated AO reviewer Task session",
        "orchestratorRules",
        "Qwen implementation",
        "no AO automation or routing",
    )

    def test_ownership_documented_in_all_docs(self):
        for doc in self.DOCS:
            with self.subTest(doc=doc):
                text = (SKILL_ROOT / doc).read_text(encoding="utf-8")
                for phrase in self.PHRASES:
                    self.assertIn(
                        phrase, text, f"{doc} missing {phrase!r}"
                    )


# ---------------------------------------------------------------------------
# v0.2.4: background-envelope transport contract
# ---------------------------------------------------------------------------


def _final_lines(stdout):
    return [ln for ln in stdout.splitlines() if ln.strip()]


class TestDirectModeExitCodes(HelperBase):
    """Direct mode retains the existing semantic process exit codes."""

    def test_pass_exits_zero(self):
        self.setup_for_disposition("pass")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.read_result()["disposition"], "pass")

    def test_blocked_exits_five(self):
        self.setup_for_disposition("blocked")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "blocked")

    def test_needs_human_exits_six(self):
        self.setup_for_disposition("needs_human")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "needs_human")

    def test_stale_exits_four(self):
        self.setup_for_disposition("stale")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "stale")

    def test_review_error_exits_three(self):
        self.setup_for_disposition("review_error")
        result = self.run_helper("5", VALID_SHA)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.read_result()["disposition"], "review_error")

    def test_usage_error_exits_two(self):
        result = self.run_helper("5", "not-a-sha")
        self.assertEqual(result.returncode, 2)


class TestBackgroundEnvelopeExits(HelperBase):
    """Qwen 0.22.3 marks a background shell task `completed` only on
    exit 0, so every trustworthy semantic disposition must exit 0 under
    the envelope, carrying its disposition in the validated result."""

    def assert_trustworthy(self, result, disposition, code):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], disposition)
        self.assertEqual(doc["semanticExitCode"], code)
        self.assertEqual(doc["invocationTransport"], "background-envelope")
        self.assertEqual(doc["contractVersion"], 6)
        lines = _final_lines(result.stdout)
        self.assertEqual(lines[-3], f"DISPOSITION={disposition}")
        self.assertEqual(lines[-2], f"SEMANTIC_EXIT_CODE={code}")
        run_dir = self.run_dirs()[-1]
        self.assertEqual(lines[-1], f"RESULT_JSON={run_dir / 'result.json'}")

    def test_pass_exits_zero(self):
        self.setup_for_disposition("pass")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "pass", 0)

    def test_blocked_exits_zero(self):
        self.setup_for_disposition("blocked")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "blocked", 5)

    def test_stale_exits_zero(self):
        self.setup_for_disposition("stale")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "stale", 4)

    def test_needs_human_exits_zero(self):
        self.setup_for_disposition("needs_human")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "needs_human", 6)

    def test_structured_review_error_exits_zero(self):
        self.setup_for_disposition("review_error")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "review_error", 3)

    def test_exit_zero_maps_to_completed_for_every_disposition(self):
        # Simulated Qwen mapping (exit 0 -> task `completed`): every
        # trustworthy semantic disposition must remain retrievable.
        for disposition, code in (
            ("pass", 0),
            ("blocked", 5),
            ("stale", 4),
            ("needs_human", 6),
            ("review_error", 3),
        ):
            base = HelperBase()
            base.setUp()
            try:
                base.setup_for_disposition(disposition)
                result = base.run_helper(
                    "--background-envelope", "5", VALID_SHA
                )
                self.assertEqual(result.returncode, 0, disposition)
                doc = base.read_result()
                self.assertEqual(
                    doc["disposition"], disposition,
                    "retrievable through the completed task",
                )
                self.assertEqual(doc["semanticExitCode"], code, disposition)
            finally:
                base.tearDown()

    def test_positional_and_args_file_forms_both_work(self):
        args_file = self.tmp / "args.txt"
        args_file.write_text(f"5 {VALID_SHA}\n", encoding="utf-8")
        self.setup_for_disposition("pass")
        positional = self.run_helper(
            "--background-envelope", "5", VALID_SHA
        )
        self.assertEqual(positional.returncode, 0, positional.stderr)
        self.assertEqual(self.read_result(0)["invocationTransport"],
                         "background-envelope")
        self.setup_for_disposition("pass")
        via_args_file = self.run_helper(
            "--background-envelope", "--args-file", str(args_file)
        )
        self.assertEqual(
            via_args_file.returncode, 0, via_args_file.stderr
        )
        self.assertEqual(self.read_result(1)["invocationTransport"],
                         "background-envelope")


class TestBackgroundEnvelopeFailurePaths(HelperBase):
    def test_invalid_usage_remains_nonzero(self):
        for args in (
            ("--background-envelope",),
            ("--background-envelope", "5", "abc"),
            ("--background-envelope", "5", VALID_SHA.upper()),
            ("--background-envelope", "5", VALID_SHA, "turbo"),
            ("--background-envelope", "--background-envelope", "5",
             VALID_SHA),
        ):
            with self.subTest(args=args):
                result = self.run_helper(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage error", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_args_file_usage_error_remains_nonzero(self):
        bad = self.tmp / "bad.txt"
        bad.write_text('"5 \n', encoding="utf-8")
        result = self.run_helper(
            "--background-envelope", "--args-file", str(bad)
        )
        self.assertEqual(result.returncode, 2)

    def test_pre_resolution_failure_exits_nonzero_without_result(self):
        self.set_gh(repo_view_fail=True)
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 3)
        lines = _final_lines(result.stdout)
        self.assertEqual(
            lines[-3:],
            ["DISPOSITION=review_error", "SEMANTIC_EXIT_CODE=3",
             "RESULT_JSON="],
        )
        self.assertFalse(self.run_dirs(), "no evidence directory may exist")

    def test_unexpected_exception_exits_nonzero(self):
        module = load_helper_module()

        def boom():
            raise RuntimeError("simulated crash")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(module, "resolve_current_repo", boom):
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = module.main(
                    ["--background-envelope", "5", VALID_SHA]
                )
        self.assertEqual(code, 3)
        stdout = out.getvalue()
        self.assertNotIn("Traceback", stdout)
        self.assertEqual(
            _final_lines(stdout)[-3:],
            ["DISPOSITION=review_error", "SEMANTIC_EXIT_CODE=3",
             "RESULT_JSON="],
        )

    def test_persistence_failure_in_envelope_mode_exits_nonzero(self):
        module = load_helper_module()
        blocker = self.tmp / "blocker"
        blocker.write_text("a directory is required under this path\n")
        run_dir = blocker / "runs" / "acme__demo" / "pr-5" / "run"
        ctx = module.new_ctx(
            run_dir,
            repository="acme/demo",
            prNumber=5,
            prUrl="https://github.com/acme/demo/pull/5",
            expectedHead=VALID_SHA,
            invocationTransport=module.TRANSPORT_BACKGROUND_ENVELOPE,
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.finish(ctx, "pass")
        self.assertEqual(code, 3)
        stdout = buffer.getvalue()
        self.assertNotIn("DISPOSITION=pass", stdout)
        self.assertEqual(
            _final_lines(stdout)[-3:],
            ["DISPOSITION=review_error", "SEMANTIC_EXIT_CODE=3",
             "RESULT_JSON="],
        )
        self.assertNotIn("Traceback", stdout)

    def test_stale_result_from_other_attempt_cannot_authorize(self):
        module = load_helper_module()
        fields = dict(
            repository="acme/demo",
            prNumber=5,
            prUrl="https://github.com/acme/demo/pull/5",
            expectedHead=VALID_SHA,
            invocationTransport=module.TRANSPORT_BACKGROUND_ENVELOPE,
        )
        run_a = self.tmp / "runs" / "acme__demo" / "pr-5" / "attempt-a"
        run_a.mkdir(parents=True)
        ctx_a = module.new_ctx(run_a, **fields)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(module.finish(ctx_a, "pass"), 0)
        stale_bytes = (run_a / "result.json").read_bytes()
        # A different run directory (different attemptId) holding a stale
        # copy of attempt A's result must not authorize transport.
        run_b = self.tmp / "runs" / "acme__demo" / "pr-5" / "attempt-b"
        run_b.mkdir(parents=True)
        (run_b / "result.json").write_bytes(stale_bytes)
        ctx_b = module.new_ctx(run_b, **fields)
        ctx_b["disposition"] = "pass"
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(
                ctx_b, run_b / "result.json"
            )

    def test_absent_empty_malformed_result_cannot_authorize(self):
        module = load_helper_module()
        run_dir = self.tmp / "runs" / "acme__demo" / "pr-5" / "attempt-x"
        run_dir.mkdir(parents=True)
        ctx = module.new_ctx(
            run_dir,
            repository="acme/demo",
            prNumber=5,
            prUrl="https://github.com/acme/demo/pull/5",
            expectedHead=VALID_SHA,
            invocationTransport=module.TRANSPORT_BACKGROUND_ENVELOPE,
        )
        ctx["disposition"] = "pass"
        path = run_dir / "result.json"
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(ctx, path)  # absent
        path.write_bytes(b"")
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(ctx, path)  # empty
        path.write_bytes(b"{not json")
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(ctx, path)  # malformed
        path.write_text(
            json.dumps({"disposition": "pass"}), encoding="utf-8"
        )
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(ctx, path)  # missing fields

    def test_killed_run_cannot_fabricate_a_trusted_result(self):
        # The fake qwen sleeps, so the helper is mid-review when it is
        # killed; no trustworthy result may exist afterwards.
        self.setup_success(sleep_seconds=5)
        proc = subprocess.Popen(
            [sys.executable, str(self.helper),
             "--background-envelope", "5", VALID_SHA],
            env=self.env,
            cwd=str(self.repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(2.0)
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
        finally:
            proc.stdout.close()
            proc.stderr.close()
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.run_dirs(), "the run directory exists mid-run")
        for run_dir in self.run_dirs():
            self.assertFalse(
                (run_dir / "result.json").is_file(),
                "a killed run must not leave a result that could "
                "authorize transport success",
            )


class TestBackgroundEnvelopeIdentity(HelperBase):
    def test_review_key_is_exact(self):
        self.setup_success()
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        self.assertEqual(doc["reviewKey"], f"acme/demo#5@{VALID_SHA}")

    def test_attempt_id_ties_result_to_current_run_dir(self):
        self.setup_success()
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        doc = self.read_result()
        run_dir = self.run_dirs()[-1]
        self.assertEqual(doc["attemptId"], run_dir.name)
        self.assertRegex(
            doc["attemptId"], r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}$"
        )

    def test_attempt_id_is_unique_per_run(self):
        self.setup_success()
        first = self.run_helper("--background-envelope", "5", VALID_SHA)
        second = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertNotEqual(
            self.read_result(0)["attemptId"],
            self.read_result(1)["attemptId"],
        )


class TestEnvelopeNeverFalsePasses(HelperBase):
    def assert_no_pass(self, result):
        self.assertNotIn("DISPOSITION=pass", result.stdout)
        for run_dir in self.run_dirs():
            result_file = run_dir / "result.json"
            if result_file.is_file():
                doc = json.loads(result_file.read_text(encoding="utf-8"))
                self.assertNotEqual(doc["disposition"], "pass")

    def test_checks_failure_is_structured_review_error(self):
        self.setup_for_disposition("review_error")
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0)
        self.assert_no_pass(result)
        self.assertEqual(self.read_result()["disposition"], "review_error")

    def test_qwen_crash_is_structured_review_error(self):
        # qwen exits 1 with empty stdout: no wrapper, not an expected
        # completed outcome -> structured review_error result, never pass.
        self.set_qwen(qwen_exit=1)
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0)
        self.assert_no_pass(result)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")

    def test_repo_mismatch_is_structured_review_error(self):
        pr = self.default_pr_doc()
        pr["url"] = "https://github.com/acme/other-repo/pull/5"
        self.set_gh(pr=pr)
        result = self.run_helper("--background-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0)
        self.assert_no_pass(result)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], "review_error")
        self.assertIn("differs from current repository", doc["error"])


# ---------------------------------------------------------------------------
# v0.2.4: envelope documentation contract
# ---------------------------------------------------------------------------


class TestEnvelopeDocs(unittest.TestCase):
    def test_skill_launch_command_uses_monitor_envelope_flag(self):
        section = _ao_skill_section("## Review execution")
        blocks = re.findall(r"```text\n(.*?)```", section, re.DOTALL)
        launch = [b for b in blocks if "run_explicit_review.py" in b]
        self.assertEqual(len(launch), 1)
        self.assertIn("--monitor-envelope", launch[0])
        self.assertIn("--args-file <injected-path>", launch[0])
        self.assertNotIn("--background-envelope", launch[0])

    def test_launch_receipt_does_not_demand_pr_or_sha(self):
        section = _ao_skill_section("## Review execution")
        self.assertIn(
            "Do not claim the PR or the expected head SHA at launch",
            section,
        )
        self.assertIn(
            "do not read, reconstruct, or invent slash",
            section,
        )
        self.assertNotIn(
            "the PR (number or URL) and the expected head SHA", section
        )

    def test_monitor_events_require_validated_semantic_result(self):
        section = _ao_skill_section("## Handling Monitor events")
        self.assertIn("semanticExitCode", section)
        self.assertIn("semantic PASS", section)

    def test_ao_reviewer_task_entry_point_is_current(self):
        section = _ao_skill_section("## AO reviewer Task entry point")
        for phrase in (
            "--monitor-envelope <PR-or-URL> <EXPECTED-SHA> "
            "[auto|medium|high]",
            "orchestrator creates",
            "one dedicated AO reviewer Task",
            "leaving the main orchestrator unblocked",
            "explicitly created by the orchestrator",
            "Explicit assignment by the orchestrator is authorization",
            "not spontaneous Skill invocation",
            "retains its AO session/task identity",
            "validates and reports `result.json` to the orchestrator",
            "create AO tasks",
            "send AO messages",
            "change AO configuration or rules",
            "route findings",
            "repair code",
            "retry automatically",
            "post to GitHub",
            "merge",
            "schedule or poll",
            "idle_timeout_ms: 600000",
            "max_events: 128",
            "agentRules",
            "orchestratorRules",
            "reviewKey",
        ):
            self.assertIn(phrase, section)

    def test_contract_version_bumped_in_docs(self):
        contract = (SKILL_ROOT / "references" / "contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("contractVersion=6", contract)
        self.assertIn("owner/repo#<PR>@<EXPECTED-40-CHAR-SHA>", contract)
        readme = (SKILL_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("VERSION=0.3.3", readme)
        self.assertIn("contractVersion=6", readme)


# ---------------------------------------------------------------------------
# v0.2.5: monitor-envelope transport (Qwen native Monitor)
# ---------------------------------------------------------------------------

MONITOR_EVENT_PREFIX = "AO_PR_REVIEW_EVENT="


def parse_monitor_events(stdout):
    """Parse a monitor protocol stream; any non-protocol line fails."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    events = []
    for line in lines:
        assert line.startswith(MONITOR_EVENT_PREFIX), (
            "monitor stdout carries only protocol events, got "
            + repr(line)
        )
        events.append(json.loads(line[len(MONITOR_EVENT_PREFIX):]))
    return events


class TestMonitorEnvelopeExits(HelperBase):
    """monitor-envelope: exit 0 only for a trustworthy, revalidated result,
    carrying the semantic disposition in the `complete` event."""

    def assert_trustworthy(self, result, disposition, code):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        events = parse_monitor_events(result.stdout)
        completes = [e for e in events if e["type"] == "complete"]
        self.assertEqual(len(completes), 1, result.stdout)
        complete = completes[0]
        self.assertEqual(complete["disposition"], disposition)
        self.assertEqual(complete["semanticExitCode"], code)
        for event in events[:-1]:
            self.assertEqual(sorted(event), ["elapsedSeconds", "type"])
            self.assertEqual(event["type"], "heartbeat")
            self.assertIsInstance(event["elapsedSeconds"], int)
        doc = self.read_result()
        self.assertEqual(doc["disposition"], disposition)
        self.assertEqual(doc["semanticExitCode"], code)
        self.assertEqual(doc["invocationTransport"], "monitor-envelope")
        self.assertEqual(doc["contractVersion"], 6)
        self.assertEqual(
            complete["resultJson"],
            str(self.run_dirs()[-1] / "result.json"),
        )
        self.assertEqual(complete["reviewKey"], doc["reviewKey"])
        self.assertEqual(complete["attemptId"], doc["attemptId"])
        return complete

    def test_args_file_form(self):
        args_file = self.tmp / "args.txt"
        args_file.write_text(f"5 {VALID_SHA}\n", encoding="utf-8")
        self.setup_for_disposition("pass")
        result = self.run_helper(
            "--monitor-envelope", "--args-file", str(args_file)
        )
        self.assert_trustworthy(result, "pass", 0)

    def test_positional_form(self):
        self.setup_for_disposition("pass")
        result = self.run_helper("--monitor-envelope", "5", VALID_SHA)
        self.assert_trustworthy(result, "pass", 0)

    def test_exit_zero_for_every_trustworthy_disposition(self):
        for disposition, code in (
            ("pass", 0),
            ("blocked", 5),
            ("stale", 4),
            ("needs_human", 6),
            ("review_error", 3),
        ):
            base = HelperBase()
            base.setUp()
            try:
                base.setup_for_disposition(disposition)
                result = base.run_helper(
                    "--monitor-envelope", "5", VALID_SHA
                )
                self.assertEqual(result.returncode, 0, disposition)
                events = parse_monitor_events(result.stdout)
                completes = [e for e in events if e["type"] == "complete"]
                self.assertEqual(len(completes), 1, disposition)
                self.assertEqual(completes[0]["disposition"], disposition)
                self.assertEqual(completes[0]["semanticExitCode"], code)
                doc = base.read_result()
                self.assertEqual(
                    doc["invocationTransport"], "monitor-envelope",
                    disposition,
                )
                self.assertEqual(doc["contractVersion"], 6, disposition)
            finally:
                base.tearDown()

    def test_complete_event_matches_result_json(self):
        self.setup_for_disposition("blocked")
        result = self.run_helper("--monitor-envelope", "5", VALID_SHA)
        complete = self.assert_trustworthy(result, "blocked", 5)
        doc = json.loads(
            Path(complete["resultJson"]).read_text(encoding="utf-8")
        )
        for field in (
            "reviewKey", "attemptId", "disposition", "semanticExitCode",
        ):
            self.assertEqual(complete[field], doc[field], field)

    def test_findings_and_prose_stay_out_of_monitor_events(self):
        sentinel = "FINDING-PROSE-MARKER-12345"
        self.setup_success(
            companion=make_companion(
                findings=[
                    {
                        "id": "R1",
                        "severity": "Critical",
                        "confidence": "low",
                        "summary": sentinel,
                        "failureScenario": sentinel,
                    }
                ]
            ),
        )
        state = json.loads(
            self.qwen_state_path.read_text(encoding="utf-8")
        )
        state["report_md_content"] = f"# report\n{sentinel}\n"
        self.qwen_state_path.write_text(json.dumps(state))
        result = self.run_helper("--monitor-envelope", "5", VALID_SHA)
        self.assertEqual(result.returncode, 0, result.stdout)
        events = parse_monitor_events(result.stdout)
        for event in events:
            self.assertNotIn(sentinel, json.dumps(event))
        self.assertEqual(events[-1]["disposition"], "needs_human")

    def test_untrustworthy_paths_exit_nonzero_without_complete(self):
        # usage error with the session active: transport_error, exit 2
        self.setup_for_disposition("pass")
        result = self.run_helper("--monitor-envelope", "5", "not-a-sha")
        self.assertEqual(
            result.returncode, 2, result.stdout + result.stderr
        )
        events = parse_monitor_events(result.stdout)
        self.assertFalse(any(e["type"] == "complete" for e in events))
        self.assertEqual(
            [e for e in events if e["type"] == "transport_error"],
            [{"type": "transport_error", "semanticExitCode": 2}],
        )
        for run_dir in self.run_dirs():
            self.assertFalse((run_dir / "result.json").is_file())

        # usage errors after session creation: transport_error, exit 2
        for args in (
            ("--monitor-envelope", "5", VALID_SHA.upper()),
            ("--monitor-envelope", "5", VALID_SHA, "turbo"),
        ):
            with self.subTest(args=args):
                result = self.run_helper(*args)
                self.assertEqual(
                    result.returncode, 2, result.stdout + result.stderr
                )
                events = parse_monitor_events(result.stdout)
                self.assertFalse(
                    any(e["type"] == "complete" for e in events)
                )
                self.assertEqual(
                    [e for e in events if e["type"] == "transport_error"],
                    [{"type": "transport_error", "semanticExitCode": 2}],
                )

        # usage errors before any session exists: no events at all
        for args in (
            ("--monitor-envelope",),
            ("--monitor-envelope", "--monitor-envelope", "5", VALID_SHA),
            ("--monitor-envelope", "--background-envelope", "5",
             VALID_SHA),
        ):
            with self.subTest(args=args):
                result = self.run_helper(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_pre_resolution_failure_emits_transport_error(self):
        self.set_gh(repo_view_fail=True)
        result = self.run_helper("--monitor-envelope", "5", VALID_SHA)
        self.assertEqual(
            result.returncode, 3, result.stdout + result.stderr
        )
        events = parse_monitor_events(result.stdout)
        self.assertEqual(
            events[-1], {"type": "transport_error", "semanticExitCode": 3}
        )
        self.assertFalse(any(e["type"] == "complete" for e in events))
        self.assertFalse(self.run_dirs(), "no evidence directory may exist")

    def test_unexpected_exception_emits_transport_error_not_prose(self):
        module = load_helper_module()

        def boom():
            raise RuntimeError("simulated crash")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(module, "resolve_current_repo", boom):
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = module.main(
                    ["--monitor-envelope", "5", VALID_SHA]
                )
        self.assertEqual(code, 3)
        events = parse_monitor_events(out.getvalue())
        self.assertEqual(
            events, [{"type": "transport_error", "semanticExitCode": 3}]
        )

    def test_cancellation_emits_no_complete_and_no_result(self):
        self.setup_success(sleep_seconds=30)
        proc = subprocess.Popen(
            [sys.executable, str(self.helper),
             "--monitor-envelope", "5", VALID_SHA],
            env=self.env,
            cwd=str(self.repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.kill)
        time.sleep(3)
        proc.kill()
        stdout, _ = proc.communicate(timeout=30)
        self.assertNotEqual(proc.returncode, 0)
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        for line in lines:
            self.assertTrue(
                line.startswith(MONITOR_EVENT_PREFIX),
                f"non-protocol line from cancelled run: {line!r}",
            )
        events = [
            json.loads(ln[len(MONITOR_EVENT_PREFIX):]) for ln in lines
        ]
        self.assertFalse(
            any(e["type"] == "complete" for e in events),
            "cancellation must not emit complete",
        )
        for run_dir in self.run_dirs():
            self.assertFalse(
                (run_dir / "result.json").is_file(),
                "cancellation must not leave a trustworthy result",
            )


class TestReviewProgress(unittest.TestCase):
    def setUp(self):
        self.module = load_helper_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="aopr-progress-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.worktree = self.home / "worktree"
        self.worktree.mkdir()
        self.outer = "outer-session"
        self.chats = self.home / ".qwen/projects/project/chats"
        self.chats.mkdir(parents=True)
        self.outer_path = self.chats / f"{self.outer}.jsonl"
        self.outer_path.write_text("{}\n", encoding="utf-8")
        self.home_patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def write_chat(self, session_id, records, mtime=None):
        path = self.chats / f"{session_id}.jsonl"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def user_record(self, session_id, timestamp="2026-09-13T10:00:00Z"):
        return {
            "type": "user", "cwd": str(self.worktree),
            "sessionId": session_id, "timestamp": timestamp,
            "message": {"role": "user", "parts": [{"text": "review"}]},
        }

    def write_subagent(self, session_id, agent_id, records):
        directory = self.chats.parent / "subagents" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"agent-review-agent-{agent_id}.jsonl"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_unique_candidate_counts_completion_from_subagent_journal(self):
        started = time.time() - 1
        sid = "inner-session"
        path = self.write_chat(sid, [self.user_record(sid)])
        progress = self.module.ReviewProgress(self.worktree, started, self.outer)
        first = progress.snapshot()
        self.assertEqual(first["stage"], "preparing")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "assistant", "timestamp": "2026-09-13T10:01:00Z",
                "message": {"parts": [{"functionCall": {
                    "id": "a1", "name": "agent", "args": {}
                }}]},
            }) + "\n")
        second = progress.snapshot()
        self.assertEqual(second["stage"], "finder_fanout")
        self.assertEqual(second["agentsStarted"], 1)
        self.assertEqual(second["agentsCompleted"], 0)
        self.write_subagent(sid, "one", [{
            "type": "assistant", "timestamp": "2026-09-13T10:02:00Z",
            "message": {"parts": [{"text": "final report"}]},
        }])
        third = progress.snapshot()
        self.assertEqual(third["stage"], "post_fanout")
        self.assertEqual(third["agentsCompleted"], 1)
        self.assertEqual(third["lastActivityAt"], "2026-09-13T10:02:00Z")

    def test_subagent_journal_with_pending_function_call_is_not_complete(self):
        started = time.time() - 1
        sid = "inner-active"
        path = self.write_chat(sid, [self.user_record(sid), {
            "type": "assistant", "timestamp": "2026-09-13T10:01:00Z",
            "message": {"parts": [{"functionCall": {
                "id": "a1", "name": "agent", "args": {}
            }}]},
        }])
        self.write_subagent(sid, "active", [{
            "type": "assistant", "timestamp": "2026-09-13T10:02:00Z",
            "message": {"parts": [{"functionCall": {
                "id": "tool1", "name": "read_file", "args": {}
            }}]},
        }])
        progress = self.module.ReviewProgress(self.worktree, started, self.outer)
        snap = progress.snapshot()
        self.assertEqual(snap["agentsStarted"], 1)
        self.assertEqual(snap["agentsCompleted"], 0)
        self.assertEqual(snap["stage"], "finder_fanout")

    def test_zero_or_ambiguous_candidates_do_not_bind(self):
        started = time.time() - 1
        progress = self.module.ReviewProgress(self.worktree, started, self.outer)
        self.assertIsNone(progress.snapshot())
        for sid in ("inner-one", "inner-two"):
            self.write_chat(sid, [self.user_record(sid)])
        self.assertIsNone(progress.snapshot())
        self.assertIsNone(progress._transcript)

    def test_old_and_wrong_cwd_candidates_are_ignored(self):
        started = time.time()
        old = self.write_chat("old-session", [self.user_record("old-session")])
        os.utime(old, (started - 10, started - 10))
        wrong = self.user_record("wrong-cwd")
        wrong["cwd"] = str(self.home / "other")
        self.write_chat("wrong-cwd", [wrong])
        progress = self.module.ReviewProgress(self.worktree, started, self.outer)
        self.assertIsNone(progress.snapshot())

    def test_malformed_complete_record_is_skipped(self):
        started = time.time() - 1
        sid = "inner-malformed"
        path = self.write_chat(sid, [self.user_record(sid)])
        with path.open("ab") as handle:
            handle.write(b"{not-json}\n")
        progress = self.module.ReviewProgress(self.worktree, started, self.outer)
        snap = progress.snapshot()
        self.assertEqual(snap["stage"], "preparing")


class TestMonitorSession(unittest.TestCase):
    """In-process unit tests for the protocol emitter; the interval is
    injected for test speed while the production constant stays 960s."""

    def test_production_heartbeat_interval_is_960_seconds(self):
        module = load_helper_module()
        self.assertEqual(module.MONITOR_HEARTBEAT_SECONDS, 960)
        self.assertEqual(module.MONITOR_KEEPALIVE_SECONDS, 480)
        session = module.MonitorSession()
        self.assertEqual(session._interval, 960)
        self.assertEqual(session._keepalive_interval, 480)

    def test_high_effort_budget_fits_below_max_events(self):
        module = load_helper_module()
        high_effort_budget = 8 * 3600  # 480-minute budget + grace
        events = -(-high_effort_budget
                   // module.MONITOR_KEEPALIVE_SECONDS)
        self.assertLess(events + 1, 128)

    def test_heartbeat_can_include_bounded_progress(self):
        module = load_helper_module()
        stream = io.StringIO()

        class Progress:
            def snapshot(self):
                return {
                    "stage": "finder_fanout",
                    "agentsStarted": 11,
                    "agentsCompleted": 4,
                    "lastActivityAt": "2026-09-13T10:00:00Z",
                }

        session = module.MonitorSession(interval=0.01, stream=stream)
        session._progress = Progress()
        session.start()
        try:
            deadline = time.monotonic() + 2
            while not stream.getvalue():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
            event = json.loads(
                stream.getvalue().splitlines()[0][len(module.MONITOR_EVENT_PREFIX):]
            )
            self.assertEqual(event["stage"], "finder_fanout")
            self.assertEqual(event["agentsStarted"], 11)
            self.assertEqual(event["agentsCompleted"], 4)
        finally:
            session._halt()

    def test_heartbeat_stops_before_terminal_event(self):
        module = load_helper_module()
        stream = io.StringIO()
        session = module.MonitorSession(
            interval=0.02, stream=stream
        ).start()
        try:
            deadline = time.monotonic() + 5
            while (
                stream.getvalue().count('"type":"heartbeat"') < 3
            ):
                self.assertLess(
                    time.monotonic(), deadline, "heartbeat never started"
                )
                time.sleep(0.01)
            session.stop_and_emit_complete(
                "/abs/path/result.json", "pass", 0,
                "acme/demo#5@" + "a" * 40, "attempt",
            )
            snapshot = stream.getvalue()
            lines = snapshot.splitlines()
            last = json.loads(
                lines[-1][len(module.MONITOR_EVENT_PREFIX):]
            )
            self.assertEqual(last["type"], "complete")
            # the stream is stable after the terminal event
            time.sleep(0.1)
            self.assertEqual(stream.getvalue(), snapshot)
        finally:
            session._halt()

    def test_stale_mismatched_attempt_never_emits_complete(self):
        module = load_helper_module()
        tmp = tempfile.TemporaryDirectory(prefix="aopr-monitor-")
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "attempt-current"
        run_dir.mkdir()
        result_path = run_dir / "result.json"
        stale_doc = {
            "contractVersion": module.CONTRACT_VERSION,
            "reviewKey": "acme/demo#5@" + "a" * 40,
            "attemptId": "attempt-previous",  # a different attempt
            "invocationTransport": "monitor-envelope",
            "disposition": "pass",
            "semanticExitCode": 0,
        }
        result_path.write_text(json.dumps(stale_doc), encoding="utf-8")
        ctx = {
            "runDir": run_dir,
            "repository": "acme/demo",
            "prNumber": 5,
            "expectedHead": "a" * 40,
            "invocationTransport": "monitor-envelope",
            "disposition": "pass",
        }
        stream = io.StringIO()
        session = module.MonitorSession(
            interval=0.05, stream=stream
        ).start()
        with self.assertRaises(module.ReviewError):
            module.validate_background_result(ctx, result_path)
        session.stop_and_emit_error(module.EXIT_REVIEW_ERROR)
        events = [
            json.loads(ln[len(module.MONITOR_EVENT_PREFIX):])
            for ln in stream.getvalue().splitlines()
            if ln.strip()
        ]
        self.assertFalse(
            any(e["type"] == "complete" for e in events),
            "a mismatched attempt must never emit complete",
        )
        self.assertEqual(
            events[-1],
            {"type": "transport_error", "semanticExitCode": 3},
        )


class TestNoFalseBackgroundNotificationClaims(unittest.TestCase):
    """v0.2.5: no doc may claim a background shell pushes a completion
    notification that resumes result handling."""

    DOCS = ("README.md", "SKILL.md", "references/contract.md")

    def test_no_doc_claims_background_shells_push_completions(self):
        for doc in self.DOCS:
            with self.subTest(doc=doc):
                text = (SKILL_ROOT / doc).read_text(encoding="utf-8")
                self.assertNotIn(
                    "background-shell completion notification resumes",
                    text,
                )
                self.assertNotIn(
                    "completion notification resumes result handling",
                    text,
                )
