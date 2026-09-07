#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

CONTEXT = "ao/semantic-review"
GH = os.environ.get("AO_OVERRIDE_GH", "gh")


def run_gh(*args: str) -> str:
    proc = subprocess.run(
        [GH, *args], text=True, capture_output=True, timeout=30
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    return proc.stdout


def gh_json(*args: str):
    try:
        return json.loads(run_gh(*args))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub CLI returned malformed JSON") from exc


def parse_invocation(text: str) -> tuple[str, str] | None:
    values = shlex.split(text.strip())
    if values == ["help"]:
        return None
    if len(values) < 2:
        raise ValueError("usage: <PR-number-or-URL> <reason>")
    pr = values[0]
    reason = " ".join(values[1:]).strip()
    if not reason:
        raise ValueError("override reason must not be empty")
    return pr, reason


def read_args_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"invalid args file: {path}")
    return path.read_text(encoding="utf-8")


def repo_name() -> str:
    data = gh_json("repo", "view", "--json", "nameWithOwner")
    name = data.get("nameWithOwner") if isinstance(data, dict) else None
    if not isinstance(name, str) or "/" not in name:
        raise RuntimeError("cannot resolve current GitHub repository")
    return name


def pr_info(pr: str, repo: str) -> dict:
    data = gh_json(
        "pr", "view", pr,
        "--repo", repo,
        "--json", "number,url,state,isDraft,headRefOid",
    )
    if not isinstance(data, dict):
        raise RuntimeError("malformed pull-request metadata")
    expected_url = f"https://github.com/{repo}/pull/{data.get('number')}"
    if data.get("url") != expected_url:
        raise RuntimeError("pull request does not belong to current repository")
    if data.get("state") != "OPEN":
        raise RuntimeError("pull request is not open")
    if data.get("isDraft") is not False:
        raise RuntimeError("pull request is draft")
    head = data.get("headRefOid")
    if not isinstance(head, str) or len(head) != 40:
        raise RuntimeError("pull request head SHA is malformed")
    return data


def lifecycle_enabled(repo: str, head: str) -> None:
    data = gh_json(
        "api", "--method", "GET",
        f"repos/{repo}/contents/.agent-harness.json",
        "-f", f"ref={head}",
    )
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        raise RuntimeError("tracked .agent-harness.json is unavailable")
    try:
        decoded = base64.b64decode(content).decode("utf-8")
        config = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("tracked .agent-harness.json is malformed") from exc
    enabled = (
        isinstance(config, dict)
        and isinstance(config.get("semanticReview"), dict)
        and config["semanticReview"].get("enabled") is True
    )
    if not enabled:
        raise RuntimeError("semantic review is not explicitly enabled on PR head")


def deterministic_checks_pass(pr: str, repo: str) -> None:
    proc = subprocess.run(
        [GH, "pr", "checks", pr, "--repo", repo, "--required",
         "--json", "name,bucket,state"],
        text=True, capture_output=True, timeout=30,
    )
    # gh exits 1 for failed checks and 8 for pending checks while still
    # returning the structured JSON needed to exclude only our own context.
    if proc.returncode not in {0, 1, 8}:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"cannot read required checks: {detail}")
    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("required-check result is malformed") from exc
    if not isinstance(checks, list):
        raise RuntimeError("required-check result is malformed")
    deterministic = [
        check for check in checks
        if isinstance(check, dict) and check.get("name") != CONTEXT
    ]
    if not deterministic:
        raise RuntimeError("no required deterministic checks found")
    bad = [
        str(check.get("name", "<unknown>"))
        for check in deterministic if check.get("bucket") != "pass"
    ]
    if bad:
        raise RuntimeError("required deterministic checks not passing: " + ", ".join(bad))


def desired_description(head: str, reason: str) -> str:
    prefix = f"Manual operator override for {head[:12]}: "
    clean = " ".join(reason.split())
    return (prefix + clean)[:140]


def latest_context_status(repo: str, head: str) -> dict | None:
    data = gh_json("api", f"repos/{repo}/commits/{head}/status")
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if not isinstance(statuses, list):
        raise RuntimeError("commit-status response is malformed")
    for status in statuses:
        if isinstance(status, dict) and status.get("context") == CONTEXT:
            return status
    return None


def publish(repo: str, pr_url: str, head: str, description: str) -> None:
    current = latest_context_status(repo, head)
    if (
        current
        and current.get("state") == "success"
        and current.get("description") == description
        and current.get("target_url") == pr_url
    ):
        return
    run_gh(
        "api", "--method", "POST", f"repos/{repo}/statuses/{head}",
        "-f", "state=success",
        "-f", f"context={CONTEXT}",
        "-f", f"description={description}",
        "-f", f"target_url={pr_url}",
    )


def verify(repo: str, pr_url: str, head: str, description: str) -> None:
    status = latest_context_status(repo, head)
    if not status:
        raise RuntimeError("override status missing after publication")
    if status.get("state") != "success":
        raise RuntimeError("override status is not successful")
    if status.get("description") != description:
        raise RuntimeError("override status description mismatch")
    if status.get("target_url") != pr_url:
        raise RuntimeError("override status target URL mismatch")


def execute(pr_ref: str, reason: str) -> None:
    repo = repo_name()
    info = pr_info(pr_ref, repo)
    head = info["headRefOid"]
    lifecycle_enabled(repo, head)
    deterministic_checks_pass(pr_ref, repo)

    live = pr_info(pr_ref, repo)
    if live["headRefOid"] != head:
        raise RuntimeError("pull request head moved before override publication")

    description = desired_description(head, reason)
    publish(repo, info["url"], head, description)
    verify(repo, info["url"], head, description)
    print(f"OVERRIDE SUCCESS: {repo}#{info['number']}@{head}")
    print(f"CONTEXT: {CONTEXT}=success")
    print(f"DESCRIPTION: {description}")


def usage() -> None:
    print("usage: /ao-semantic-review-override <PR-number-or-URL> <reason>")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?")
    parser.add_argument("--args-file", type=Path)
    ns, extra = parser.parse_known_args()
    try:
        if ns.command == "help" and ns.args_file is None and not extra:
            usage()
            return 0
        if ns.args_file is None or ns.command is not None or extra:
            raise ValueError("real invocation requires only --args-file <path>")
        parsed = parse_invocation(read_args_file(ns.args_file))
        if parsed is None:
            usage()
            return 0
        execute(*parsed)
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"override refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
