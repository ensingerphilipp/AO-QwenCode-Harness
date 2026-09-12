#!/usr/bin/env python3
"""ao-pr-review helper.

Runs exactly one explicit, non-posting native Qwen semantic review of an
exact GitHub PR head after required CI passes. Effort (medium/high) is
selected deterministically by a local policy; no model call is used for
selection. All evidence is written under the state root
(~/.local/state/ao-pr-review by default, overridable for
maintenance via AO_PR_REVIEW_STATE_DIR).

Safety contract (see SKILL.md, references/contract.md, references/policy.md):
  * non-posting and no intended tracked application changes;
  * never repairs, routes, retries, or merges;
  * fails closed on any malformed, incomplete, or ambiguous input.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_VERSION = 6
EFFORT_POLICY_VERSION = 2
# Commit status context the AO orchestrator publishes after each semantic
# review. The required-CI preflight excludes exactly this context (exact
# string match only): it can only pass as a result of a review, so treating
# it as a readiness prerequisite would self-block the review that is the
# only thing able to pass it.
SEMANTIC_REVIEW_CONTEXT = "ao/semantic-review"
TRANSPORT_DIRECT = "direct"
TRANSPORT_BACKGROUND_ENVELOPE = "background-envelope"
TRANSPORT_MONITOR_ENVELOPE = "monitor-envelope"
# Qwen Monitor hard-caps idle_timeout_ms at 600000 (10 minutes). A tiny
# transport-only keepalive therefore remains below that cap, while the richer
# observational progress heartbeat is intentionally less frequent.
MONITOR_KEEPALIVE_SECONDS = 480
MONITOR_HEARTBEAT_SECONDS = 960
MONITOR_EVENT_PREFIX = "AO_PR_REVIEW_EVENT="
DEFAULT_STATE_ROOT = Path.home() / ".local/state/ao-pr-review"
STATE_ROOT_ENV = "AO_PR_REVIEW_STATE_DIR"
PROJECT_RISK_CONFIG = Path(".qwen/review-config.json")
PROJECT_RISK_CONFIG_MAX_BYTES = 64 * 1024

ARGS_FILE_MAX_BYTES = 4096
PATCH_MAX_BYTES = 512 * 1024
GH_TIMEOUT_SECONDS = 120
# Deterministic effort-based native review budget (v0.2.3): selected
# medium -> 240 minutes, selected high -> 480 minutes.
NATIVE_TIMEOUT_MINUTES = {
    "medium": 240,
    "high": 480,
}
# Wrapper cleanup grace beyond the native budget (seconds).
WRAPPER_CLEANUP_GRACE_SECONDS = 600
LARGE_CHANGE_FILE_THRESHOLD = 15
LARGE_CHANGE_LINE_THRESHOLD = 500

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PR_URL_RE = re.compile(
    r"^https://github.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)$"
)
GITHUB_URL_RE = re.compile(r"^https://github.com/.+")

ALLOWED_EVENTS = ("APPROVE", "COMMENT", "REQUEST_CHANGES")
SEVERITY_VOCAB = {"Critical", "Suggestion", "Nice to have"}
CONFIDENCE_VOCAB = {"high", "low"}
SOURCE_VOCAB = {"review", "build", "test", "probe", "lint"}
OUTCOME_VOCAB = {"fixed", "skipped", "no_change_needed"}
UNRESOLVED_OUTCOMES = (None, "skipped")
SAFE_INT_MAX = 2**53 - 1

EXIT_PASS = 0
EXIT_USAGE = 2
EXIT_REVIEW_ERROR = 3
EXIT_STALE = 4
EXIT_BLOCKED = 5
EXIT_NEEDS_HUMAN = 6

DISPOSITION_EXIT = {
    "pass": EXIT_PASS,
    "review_error": EXIT_REVIEW_ERROR,
    "stale": EXIT_STALE,
    "blocked": EXIT_BLOCKED,
    "needs_human": EXIT_NEEDS_HUMAN,
}

DISPOSITION_LABEL = {
    "pass": "PASS",
    "blocked": "BLOCKED",
    "needs_human": "NEEDS HUMAN",
    "stale": "STALE",
    "review_error": "REVIEW ERROR",
}

EARLY_NEXT_ACTION = (
    "Inspect the evidence directory, resolve the reported failure, and "
    "re-run manually."
)

USAGE = """\
ao-pr-review: one explicit, non-posting native Qwen semantic review of an exact PR head.

Usage (skill):
  /ao-pr-review <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
  /ao-pr-review help

Helper:
  run_explicit_review.py --args-file <injected-args-file>
  run_explicit_review.py <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
  run_explicit_review.py --background-envelope --args-file <injected-args-file>
  run_explicit_review.py --background-envelope <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
  run_explicit_review.py --monitor-envelope --args-file <injected-args-file>
  run_explicit_review.py --monitor-envelope <PR-number-or-URL> <EXPECTED-40-CHAR-HEAD-SHA> [auto|medium|high]
  run_explicit_review.py help

Rules:
  * The expected head SHA must be exactly 40 lowercase hexadecimal characters.
  * The default effort request is auto (deterministic medium/high selection).
  * This skill is non-posting and makes no intended tracked application changes.
  * --background-envelope keeps the same result contract but exits 0
    whenever a trustworthy result.json was persisted and validated, for
    any disposition; direct mode keeps the semantic exit codes.
  * --monitor-envelope uses the same result contract and exit policy as
    --background-envelope, but streams bounded AO_PR_REVIEW_EVENT= protocol
    lines (transport keepalives, bounded progress heartbeats, and exactly one
    terminal event) so Qwen's native Monitor tool can deliver notifications to the session
    that launched the review.
"""

# ---------------------------------------------------------------------------
# Deterministic effort policy (policy v2). No model calls.
# ---------------------------------------------------------------------------

BASE_HIGH_RISK_PATH_PATTERNS = (
    "PROJECT.md",
    "ARCHITECTURE.md",
    "QWEN.md",
    ".qwen/review-rules.md",
    ".qwen/review-config.json",
    "scripts/verify",
    ".github/workflows/**",
)

BASE_HIGH_RISK_LABELS = {
    "security",
    "architecture",
    "breaking-change",
    "release-critical",
    "risk:high",
    "concurrency",
}

TITLE_BODY_RISK_MARKERS = (
    "risk:high",
    "high-risk",
    "high risk",
    "breaking change",
    "breaking-change",
    "security",
)

RISK_TERM_GROUPS = (
    (
        "concurrency",
        (
            r"\bgoroutine",
            r"\bgo\s+func\b",
            r"\bchannel",
            r"\bchan\b",
            r"\bmutex",
            r"\bsync\.(Mutex|RWMutex|WaitGroup|Once|Cond)\b",
            r"\batomic\.",
            r"\brace\b",
            r"\bdeadlock",
            r"\bscheduling",
            r"\bstate[-_ ]?machine",
        ),
    ),
    (
        "authentication/permissions",
        (
            r"\bauth(entication|orization)?\b",
            r"\bpermission",
            r"\bsecret",
            r"\bcredential",
            r"\btoken\b",
            r"\bpassword",
            r"private key",
            r"api key",
        ),
    ),
    (
        "tls/cryptography",
        (
            r"\btls\b",
            r"\bcertificate",
            r"\bx509\b",
            r"\bcrypto\b",
            r"\bencrypt",
            r"\bdecrypt",
            r"\bhmac\b",
        ),
    ),
    (
        "persistence/schema",
        (
            r"\bmigrat",
            r"\bschema",
            r"\bdatabase",
            r"\bddl\b",
            r"\bpersisten",
            r"\bsqlite",
            r"\bpostgres",
            r"\bmysql",
        ),
    ),
)


def _compile_path_pattern(pattern: str) -> "re.Pattern":
    if pattern.endswith("/**"):
        prefix = pattern[:-2]  # drop the "**", keep the trailing "/"
        return re.compile(re.escape(prefix) + "[^/]+(?:/[^/]+)*$")
    if "*" in pattern:
        parts = pattern.split("*")
        return re.compile("^" + "[^/]*".join(re.escape(p) for p in parts) + "$")
    return re.compile("^" + re.escape(pattern) + "$")


_COMPILED_BASE_PATH_PATTERNS = tuple(
    (_compile_path_pattern(p), p) for p in BASE_HIGH_RISK_PATH_PATTERNS
)
_COMPILED_RISK_TERMS = tuple(
    (group, tuple(re.compile(term, re.IGNORECASE) for term in terms))
    for group, terms in RISK_TERM_GROUPS
)



def load_project_risk_config(repo_root: Path):
    """Load the optional tracked repository risk extension from trusted HEAD.

    Local untracked, staged-only, modified, or symlinked policy must never
    influence effort selection. The later repository guard independently
    refuses tracked/staged worktree changes before Qwen launches.
    """
    path = repo_root / PROJECT_RISK_CONFIG
    rel = PROJECT_RISK_CONFIG.as_posix()
    meta = {"relativePath": rel, "present": False, "sha256": None}

    listed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "HEAD", "--", rel],
        capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False,
    )
    if listed.returncode != 0:
        raise ValueError(f"cannot inspect tracked {PROJECT_RISK_CONFIG}: {listed.stderr.strip()}")
    line = listed.stdout.strip()
    if not line:
        if path.exists() or path.is_symlink():
            raise ValueError(f"untracked {PROJECT_RISK_CONFIG} is not trusted policy")
        return {"highRiskPaths": (), "highRiskLabels": ()}, meta

    # Expected: <mode> <type> <object>	<path>
    try:
        left, tracked_path = line.split("\t", 1)
        mode, obj_type, _object_id = left.split(" ", 2)
    except ValueError as exc:
        raise ValueError(f"unexpected git metadata for {PROJECT_RISK_CONFIG}") from exc
    if tracked_path != rel or obj_type != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(f"{PROJECT_RISK_CONFIG} must be a tracked regular file")

    shown = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"HEAD:{rel}"],
        capture_output=True, timeout=GH_TIMEOUT_SECONDS, check=False,
    )
    if shown.returncode != 0:
        err = shown.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot read tracked {PROJECT_RISK_CONFIG}: {err}")
    raw = shown.stdout
    if len(raw) > PROJECT_RISK_CONFIG_MAX_BYTES:
        raise ValueError(
            f"{PROJECT_RISK_CONFIG} exceeds {PROJECT_RISK_CONFIG_MAX_BYTES} bytes"
        )
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{PROJECT_RISK_CONFIG} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{PROJECT_RISK_CONFIG} root must be a JSON object")
    allowed = {"schemaVersion", "highRiskPaths", "highRiskLabels"}
    unknown = sorted(set(doc) - allowed)
    if unknown:
        raise ValueError(f"{PROJECT_RISK_CONFIG} has unknown keys: {unknown}")
    if doc.get("schemaVersion") != 1 or isinstance(doc.get("schemaVersion"), bool):
        raise ValueError(f"{PROJECT_RISK_CONFIG} schemaVersion must be exactly 1")

    def string_list(name):
        value = doc.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"{PROJECT_RISK_CONFIG} {name} must be an array")
        out = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{PROJECT_RISK_CONFIG} {name} entries must be non-empty strings")
            item = item.strip()
            if len(item) > 256:
                raise ValueError(f"{PROJECT_RISK_CONFIG} {name} entry exceeds 256 characters")
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
                raise ValueError(f"{PROJECT_RISK_CONFIG} {name} entries must not contain control characters")
            out.append(item)
        if len(out) > 128:
            raise ValueError(f"{PROJECT_RISK_CONFIG} {name} may contain at most 128 entries")
        if len(set(out)) != len(out):
            raise ValueError(f"{PROJECT_RISK_CONFIG} {name} entries must be unique")
        return out

    paths = string_list("highRiskPaths")
    for pattern in paths:
        pp = Path(pattern)
        if pp.is_absolute() or "\\" in pattern or any(part == ".." for part in pp.parts):
            raise ValueError(
                f"{PROJECT_RISK_CONFIG} highRiskPaths must be repository-relative POSIX patterns: {pattern!r}"
            )
        _compile_path_pattern(pattern)
    labels = [label.lower() for label in string_list("highRiskLabels")]
    if len(set(labels)) != len(labels):
        raise ValueError(f"{PROJECT_RISK_CONFIG} highRiskLabels must be unique case-insensitively")
    meta.update({"present": True, "sha256": hashlib.sha256(raw).hexdigest()})
    return {"highRiskPaths": tuple(paths), "highRiskLabels": tuple(labels)}, meta

def select_effort(requested, pr_view, patch, patch_error, project_risk=None):
    """Deterministic medium/high selection (policy v2). Returns (effort, reasons)."""
    reasons = []
    project_risk = project_risk or {"highRiskPaths": (), "highRiskLabels": ()}
    compiled_paths = _COMPILED_BASE_PATH_PATTERNS + tuple(
        (_compile_path_pattern(p), p) for p in project_risk.get("highRiskPaths", ())
    )
    high_risk_labels = BASE_HIGH_RISK_LABELS | set(project_risk.get("highRiskLabels", ()))

    file_paths = pr_view.get("filePaths") or []
    labels = pr_view.get("labels") or []
    title = pr_view.get("title") or ""
    body = pr_view.get("body") or ""
    changed_files = pr_view.get("changedFiles")
    additions = pr_view.get("additions")
    deletions = pr_view.get("deletions")

    # Incomplete risk metadata or inability to inspect the diff safely.
    if changed_files is None or additions is None or deletions is None:
        reasons.append("incomplete risk metadata: PR size fields missing")
    if not pr_view.get("filesMetadataValid", False):
        reasons.append(
            "incomplete risk metadata: files metadata missing, wrong-type, "
            "or malformed"
        )
    if not pr_view.get("labelsMetadataValid", False):
        reasons.append(
            "incomplete risk metadata: labels metadata missing, wrong-type, "
            "or malformed"
        )
    if changed_files is not None and changed_files > len(file_paths):
        reasons.append(
            "incomplete risk metadata: changedFiles="
            f"{changed_files} exceeds the {len(file_paths)} valid file paths "
            "returned; file list treated as incomplete"
        )

    # High-risk paths.
    for regex, pattern in compiled_paths:
        for path in file_paths:
            if regex.match(path):
                reasons.append(f"high-risk path: {pattern} (matched '{path}')")
                break

    # High-risk labels (case-insensitive).
    for label in labels:
        if isinstance(label, str) and label.strip().lower() in high_risk_labels:
            reasons.append(f"high-risk label: {label.strip().lower()}")

    # Large or unusually broad changes.
    if changed_files is not None and changed_files >= LARGE_CHANGE_FILE_THRESHOLD:
        reasons.append(
            f"large change: changedFiles={changed_files} >= "
            f"{LARGE_CHANGE_FILE_THRESHOLD}"
        )
    if (
        additions is not None
        and deletions is not None
        and additions + deletions >= LARGE_CHANGE_LINE_THRESHOLD
    ):
        reasons.append(
            "large change: additions+deletions="
            f"{additions + deletions} >= {LARGE_CHANGE_LINE_THRESHOLD}"
        )

    # Explicit high-risk title/body markers.
    haystack = f"{title}\n{body}".lower()
    for marker in TITLE_BODY_RISK_MARKERS:
        if marker in haystack:
            reasons.append(f"title/body risk marker: {marker}")

    # Bounded patch inspection.
    if patch_error:
        reasons.append(f"incomplete risk metadata: {patch_error}")
    elif patch:
        for group, patterns in _COMPILED_RISK_TERMS:
            for pattern in patterns:
                match = pattern.search(patch)
                if match:
                    reasons.append(
                        f"patch risk term: {group} (matched '{match.group(0).strip()}')"
                    )
                    break

    if requested == "high":
        return "high", ["explicit high effort requested"] + reasons
    if requested == "medium":
        if reasons:
            return "high", ["explicit medium promoted to high"] + reasons
        return "medium", ["no high-risk rules matched (policy v2)"]
    if reasons:
        return "high", reasons
    return "medium", ["no high-risk rules matched (policy v2)"]


def native_timeout_plan(selected_effort: str):
    """Deterministic (native minutes, wrapper seconds) for the selected effort.

    Native minutes: medium -> 240, high -> 480. The wrapper timeout is the
    native budget plus 600 seconds of cleanup grace.
    """
    minutes = NATIVE_TIMEOUT_MINUTES[selected_effort]
    return minutes, minutes * 60 + WRAPPER_CLEANUP_GRACE_SECONDS


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UsageError(Exception):
    """Invocation-level failure (exit 2). No evidence directory is required."""


class ReviewError(Exception):
    """Runtime failure producing disposition review_error (exit 3)."""


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, doc) -> None:
    atomic_write_bytes(path, (json.dumps(doc, indent=2, sort_keys=False) + "\n").encode("utf-8"))


def copy_bytes(src: Path, dst: Path) -> None:
    atomic_write_bytes(dst, src.read_bytes())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Invocation argument handling
# ---------------------------------------------------------------------------


def read_args_file(raw_path: str) -> list:
    """Read the Qwen-injected args file once; fail closed on any problem.

    The raw contents are never printed.
    """
    if not raw_path:
        raise UsageError("missing --args-file path")
    path = Path(raw_path)
    if path.is_symlink():
        raise UsageError("args file is a symlink; refusing to read it")
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise UsageError(f"args file missing or unreadable: {exc}")
    if not stat.S_ISREG(st.st_mode):
        raise UsageError("args file is not a regular file")
    if st.st_size > ARGS_FILE_MAX_BYTES:
        raise UsageError(f"args file exceeds the {ARGS_FILE_MAX_BYTES} byte limit")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"args file unreadable: {exc}")
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise UsageError(f"args file is malformed: {exc}")
    if not tokens:
        raise UsageError("args file is empty or stale-looking; refusing to guess")
    if len(tokens) == 1 and tokens[0] != "help":
        raise UsageError(
            "args file must contain exactly 'help' or 2-3 review arguments "
            "(got 1 token)"
        )
    if len(tokens) > 3:
        raise UsageError(
            f"unsupported number of arguments: {len(tokens)} (expected 2 or 3)"
        )
    return tokens


# ---------------------------------------------------------------------------
# GitHub reads (read-only gh subcommands only)
# ---------------------------------------------------------------------------

PR_VIEW_FIELDS = (
    "number,url,state,isDraft,headRefOid,additions,deletions,changedFiles,"
    "files,labels,title,body"
)


def run_gh(argv: list) -> "subprocess.CompletedProcess":
    try:
        return subprocess.run(
            ["gh", *argv],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            env=os.environ,
        )
    except FileNotFoundError as exc:
        raise ReviewError(f"gh executable not found: {exc}")
    except subprocess.SubprocessError as exc:
        raise ReviewError(f"gh invocation failed: {exc}")


def gh_json(argv: list, what: str):
    proc = run_gh(argv)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
        raise ReviewError(
            f"{what}: gh exited {proc.returncode}" + (f": {detail}" if detail else "")
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ReviewError(f"{what}: gh output is not valid JSON")


def resolve_current_repo() -> str:
    doc = gh_json(["repo", "view", "--json", "owner,name"], "resolve current repository")
    owner = (doc.get("owner") or {}).get("login") if isinstance(doc, dict) else None
    name = doc.get("name") if isinstance(doc, dict) else None
    if not isinstance(owner, str) or not isinstance(name, str):
        raise ReviewError(
            "resolve current repository: owner/name missing from gh repo view output"
        )
    return f"{owner}/{name}"


def read_pr_head(canonical_url: str):
    """Return the 40-char head SHA, or None when the read yields an invalid value."""
    doc = gh_json(["pr", "view", canonical_url, "--json", "headRefOid"], "read PR head")
    head = doc.get("headRefOid") if isinstance(doc, dict) else None
    if not isinstance(head, str) or not SHA_RE.match(head):
        return None
    return head


def take_checks_snapshot(canonical_url: str) -> dict:
    proc = run_gh(
        ["pr", "checks", canonical_url, "--required", "--json", "name,state,bucket"]
    )
    snapshot = {
        "command": f"gh pr checks {canonical_url} --required --json name,state,bucket",
        "exitCode": proc.returncode,
        "checks": [],
    }
    if proc.returncode != 0:
        snapshot["stderr"] = (proc.stderr or "").strip()[:2000]
    # gh exits nonzero (1 = a required check failed, 8 = checks pending)
    # while stdout still carries the JSON array, so the structured list is
    # parsed regardless of the exit code; the exit code and stderr stay in
    # the snapshot as evidence but never gate the preflight decision.
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        snapshot["parseError"] = "checks output is not valid JSON"
    else:
        if isinstance(parsed, list):
            snapshot["checks"] = parsed
        else:
            snapshot["parseError"] = "checks output is not a JSON array"
    return snapshot


def fetch_patch(canonical_url: str):
    """Bounded PR patch for risk-term inspection. Returns (patch, error)."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "diff", canonical_url],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            env=os.environ,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return None, f"patch retrieval failed ({type(exc).__name__})"
    if proc.returncode != 0:
        return None, f"patch retrieval failed (gh pr diff exit {proc.returncode})"
    if len(proc.stdout.encode("utf-8", "replace")) > PATCH_MAX_BYTES:
        return None, f"patch exceeds safe inspection bound ({PATCH_MAX_BYTES} bytes)"
    return proc.stdout, None


def policy_view(pr_doc: dict) -> dict:
    """Extract risk metadata from real `gh pr view --json files,labels` output.

    gh 2.98.0 emits flattened arrays, not GraphQL-style `{"nodes": [...]}`
    wrappers:
        "files":  [{"path": "package-manifest"}, ...]
        "labels": [{"name": "security"}, ...]
    Every valid path/name is extracted; missing, wrong-type, or malformed
    metadata is flagged so the selector fails closed (high).
    """
    files = pr_doc.get("files")
    file_paths = []
    files_valid = isinstance(files, list)
    if isinstance(files, list):
        for entry in files:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("path"), str)
                and entry.get("path")
            ):
                file_paths.append(entry["path"])
            else:
                files_valid = False
    labels = pr_doc.get("labels")
    label_names = []
    labels_valid = isinstance(labels, list)
    if isinstance(labels, list):
        for entry in labels:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and entry.get("name")
            ):
                label_names.append(entry["name"])
            else:
                labels_valid = False
    return {
        "filePaths": file_paths,
        "labels": label_names,
        "changedFiles": pr_doc.get("changedFiles"),
        "additions": pr_doc.get("additions"),
        "deletions": pr_doc.get("deletions"),
        "title": pr_doc.get("title") or "",
        "body": pr_doc.get("body") or "",
        "filesMetadataValid": files_valid,
        "labelsMetadataValid": labels_valid,
    }


# ---------------------------------------------------------------------------
# Repository guard
# ---------------------------------------------------------------------------


def repo_toplevel() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git invocation failed: {exc}")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ReviewError("current working directory is not inside a git repository")
    return proc.stdout.strip()


def tracked_status(toplevel: str) -> list:
    """Tracked/staged porcelain entries; untracked (??) entries are ignored."""
    try:
        proc = subprocess.run(
            ["git", "-C", toplevel, "status", "--porcelain"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git invocation failed: {exc}")
    if proc.returncode != 0:
        raise ReviewError("git status failed while recording repository guard state")
    entries = []
    for line in proc.stdout.splitlines():
        if len(line) >= 3 and not (line[0] == "?" and line[1] == "?"):
            entries.append(line)
    return sorted(entries)


def read_local_head(toplevel: str) -> str:
    """Local Git HEAD commit (read-only rev-parse)."""
    try:
        proc = subprocess.run(
            ["git", "-C", toplevel, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git invocation failed: {exc}")
    if proc.returncode != 0:
        raise ReviewError(f"git rev-parse HEAD failed (exit {proc.returncode})")
    head = proc.stdout.strip()
    if not SHA_RE.match(head):
        raise ReviewError("git rev-parse HEAD did not return a 40-character SHA")
    return head


def read_local_ref(toplevel: str) -> str:
    """Short symbolic branch name, or a deterministic detached-HEAD
    representation when HEAD is not on a branch."""
    try:
        proc = subprocess.run(
            ["git", "-C", toplevel, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            env=os.environ,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git invocation failed: {exc}")
    if proc.returncode == 0:
        ref = proc.stdout.strip()
        if ref:
            return ref
    return f"detached-HEAD@{read_local_head(toplevel)}"


def read_local_identity(toplevel: str) -> dict:
    """Read-only local repository identity snapshot: tracked/staged status
    entries, the local HEAD commit, and the symbolic branch/ref.

    Only read-only git commands are used (status, rev-parse,
    symbolic-ref); the helper never adds, commits, checks out, resets,
    cleans, or restores.
    """
    return {
        "trackedStatus": tracked_status(toplevel),
        "head": read_local_head(toplevel),
        "ref": read_local_ref(toplevel),
    }


# ---------------------------------------------------------------------------
# Qwen 0.22.3 artifact validation (strict)
# ---------------------------------------------------------------------------


def extract_wrapper(stdout: str):
    """Locate the wrapper JSON object in the current `qwen review run --json`
    stdout only: the entire stdout, or the last valid JSON-object line of the
    same stdout.

    A pre-existing repository-root qwen-run.json must never influence a new
    run; empty or invalid current stdout yields None (review_error).
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict):
        return doc
    for line in reversed([ln for ln in (stdout or "").splitlines() if ln.strip()]):
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return None


def validate_wrapper(wrapper) -> list:
    errors = []
    if not isinstance(wrapper, dict):
        return ["wrapper is not a JSON object"]
    if wrapper.get("completed") is not True:
        errors.append("wrapper completed is not true")
    if wrapper.get("timedOut") is not False:
        errors.append("wrapper timedOut is not false")
    report_path = wrapper.get("reportPath")
    if not isinstance(report_path, str) or not report_path or not report_path.endswith(".md"):
        errors.append("wrapper reportPath is not a non-empty string ending in .md")
    if wrapper.get("event") not in ALLOWED_EVENTS:
        errors.append(f"wrapper event is not one of {list(ALLOWED_EVENTS)}")
    if wrapper.get("baseEvent") not in ALLOWED_EVENTS:
        errors.append(f"wrapper baseEvent is not one of {list(ALLOWED_EVENTS)}")
    capped_by = wrapper.get("cappedBy")
    if not isinstance(capped_by, list) or not all(isinstance(x, str) for x in capped_by):
        errors.append("wrapper cappedBy is not an array of strings")
    return errors


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _non_neg_int(value) -> bool:
    """A valid non-negative safe integer (booleans are not integers)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= SAFE_INT_MAX
    )


def validate_counts(counts) -> list:
    errors = []
    if not isinstance(counts, dict):
        return ["companion counts is not an object"]
    if not _non_neg_int(counts.get("total")):
        errors.append(
            "companion counts.total is not a valid non-negative integer"
        )
    for label, keys in (
        ("bySeverity", ("Critical", "Suggestion", "Nice to have")),
        ("byConfidence", ("high", "low")),
    ):
        section = counts.get(label)
        if not isinstance(section, dict):
            errors.append(f"companion counts.{label} is not an object")
        else:
            for key in keys:
                if not _non_neg_int(section.get(key)):
                    errors.append(
                        f"companion counts.{label}.{key} is not a valid "
                        "non-negative integer"
                    )
    if not _non_neg_int(counts.get("held")):
        errors.append(
            "companion counts.held is not a valid non-negative integer"
        )
    if "byOutcome" in counts:
        section = counts["byOutcome"]
        if not isinstance(section, dict):
            errors.append("companion counts.byOutcome is not an object")
        else:
            for key in OUTCOME_VOCAB:
                if not _non_neg_int(section.get(key)):
                    errors.append(
                        f"companion counts.byOutcome.{key} is not a valid "
                        "non-negative integer"
                    )
    return errors


def validate_markdown_report_path(path) -> list:
    if not _non_empty_str(path):
        return [
            "companion markdownReportPath is not a non-empty string"
        ]
    errors = []
    if path.startswith("/"):
        errors.append(
            "companion markdownReportPath must be a relative path"
        )
    if ".." in path.split("/"):
        errors.append(
            "companion markdownReportPath must not contain a '..' segment"
        )
    if not path.endswith(".md"):
        errors.append(
            "companion markdownReportPath must end in .md"
        )
    if not path.startswith(".qwen/reviews/"):
        errors.append(
            "companion markdownReportPath must be under .qwen/reviews/"
        )
    return errors


def validate_companion(doc, pr_number: int, selected_effort: str) -> list:
    """Strict validation aligned with the Qwen Code v0.22.3 canonical
    artifact parser. Unknown fields pass through unchecked and are
    preserved verbatim."""
    errors = []
    if not isinstance(doc, dict):
        return ["companion is not a JSON object"]
    schema = doc.get("schemaVersion")
    if not (isinstance(schema, int) and not isinstance(schema, bool) and schema == 1):
        errors.append("companion schemaVersion is not exactly 1")
    if doc.get("target") != f"pr-{pr_number}":
        errors.append(f"companion target is not 'pr-{pr_number}'")
    if doc.get("effort") != selected_effort:
        errors.append(
            f"companion effort {doc.get('effort')!r} does not match selected effort "
            f"{selected_effort!r}"
        )
    verdict = doc.get("verdict")
    if not isinstance(verdict, dict):
        errors.append("companion verdict is not an object")
    else:
        if verdict.get("event") not in ALLOWED_EVENTS:
            errors.append(
                f"companion verdict.event {verdict.get('event')!r} is not one "
                f"of {list(ALLOWED_EVENTS)}"
            )
        if verdict.get("baseEvent") not in ALLOWED_EVENTS:
            errors.append(
                f"companion verdict.baseEvent {verdict.get('baseEvent')!r} is "
                f"not one of {list(ALLOWED_EVENTS)}"
            )
        verdict_capped = verdict.get("cappedBy")
        if not isinstance(verdict_capped, list) or not all(
            isinstance(x, str) for x in verdict_capped
        ):
            errors.append("companion verdict.cappedBy is not an array of strings")
        if not _non_empty_str(verdict.get("verdictLine")):
            errors.append(
                "companion verdict.verdictLine is not a non-empty string"
            )
    if not isinstance(doc.get("findings"), list):
        errors.append("companion findings is not an array")
    errors.extend(validate_counts(doc.get("counts")))
    errors.extend(validate_markdown_report_path(doc.get("markdownReportPath")))
    return errors


def _validate_location(location, label: str) -> list:
    errors = []
    if not isinstance(location, dict):
        return [f"{label} is not an object"]
    if not _non_empty_str(location.get("file")):
        errors.append(f"{label}.file is not a non-empty string")
    if "line" in location:
        line = location["line"]
        if (
            not isinstance(line, int)
            or isinstance(line, bool)
            or line <= 0
            or line > SAFE_INT_MAX
        ):
            errors.append(f"{label}.line is not a positive integer")
    if "anchor" in location and not _non_empty_str(location["anchor"]):
        errors.append(f"{label}.anchor is not a non-empty string")
    return errors


def validate_findings(findings: list) -> list:
    """Strict finding validation aligned with the Qwen Code v0.22.3
    canonical artifact parser. Unknown fields pass through unchecked and
    are preserved verbatim."""
    errors = []
    seen_outcome = False
    for index, finding in enumerate(findings):
        label = f"finding[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{label} is not an object")
            continue
        if not _non_empty_str(finding.get("id")):
            errors.append(f"{label}.id is not a non-empty string")
        severity = finding.get("severity")
        if severity not in SEVERITY_VOCAB:
            errors.append(
                f"{label}.severity {severity!r} is not in the contract "
                f"vocabulary {sorted(SEVERITY_VOCAB)}"
            )
        confidence = finding.get("confidence")
        if confidence not in CONFIDENCE_VOCAB:
            errors.append(
                f"{label}.confidence {confidence!r} is not in the contract "
                f"vocabulary {sorted(CONFIDENCE_VOCAB)}"
            )
        source = finding.get("source")
        if source not in SOURCE_VOCAB:
            errors.append(
                f"{label}.source {source!r} is not in the contract "
                f"vocabulary {sorted(SOURCE_VOCAB)}"
            )
        for field in ("summary", "shortSummary", "failureScenario"):
            if not _non_empty_str(finding.get(field)):
                errors.append(f"{label}.{field} is not a non-empty string")
        locations = finding.get("locations")
        if not isinstance(locations, list) or not locations:
            errors.append(f"{label}.locations is not a non-empty array")
        else:
            for loc_index, location in enumerate(locations):
                errors.extend(
                    _validate_location(
                        location, f"{label}.locations[{loc_index}]"
                    )
                )
        for field in ("witness", "suggestedFix", "category", "outcomeNote"):
            if field in finding and not _non_empty_str(finding[field]):
                errors.append(f"{label}.{field} is not a non-empty string")
        for field in ("assetFiles", "assets"):
            if field in finding:
                value = finding[field]
                if not isinstance(value, list) or not all(
                    _non_empty_str(x) for x in value
                ):
                    errors.append(
                        f"{label}.{field} is not an array of non-empty strings"
                    )
        if "heldByMeasurement" in finding:
            held = finding["heldByMeasurement"]
            if not isinstance(held, dict) or not _non_empty_str(
                held.get("file")
            ):
                errors.append(
                    f"{label}.heldByMeasurement is not an object with a "
                    "non-empty file"
                )
        if "outcome" in finding:
            seen_outcome = True
            if finding["outcome"] not in OUTCOME_VOCAB:
                errors.append(
                    f"{label}.outcome {finding['outcome']!r} is not in "
                    f"{sorted(OUTCOME_VOCAB)}"
                )
    if seen_outcome:
        for index, finding in enumerate(findings):
            if isinstance(finding, dict) and "outcome" not in finding:
                errors.append(
                    f"finding[{index}] has no outcome while other findings carry "
                    "outcomes (partially populated outcomes)"
                )
    return errors


def compute_disposition(
    *,
    expected: str,
    head_after,
    head_read_failed: bool,
    run_ok: bool,
    qwen_exit: int,
    event,
    base_event,
    findings: list,
):
    """Apply the disposition policy in its strict order. Returns (disposition, error, next_action)."""
    if head_after is not None and head_after != expected:
        return (
            "stale",
            None,
            f"Head moved during review (expected {expected}, observed "
            f"{head_after}); the old verdict is invalidated. Re-run /ao-pr-review "
            "against the new head SHA.",
        )
    if head_read_failed or head_after is None:
        return (
            "review_error",
            "post-review head could not be retrieved or is invalid",
            "Inspect the evidence, verify the PR head manually, and re-run.",
        )
    if not run_ok:
        return (
            "review_error",
            None,
            "Inspect the evidence directory, resolve the reported validation "
            "failure, and re-run.",
        )
    if qwen_exit == 3 or event == "REQUEST_CHANGES":
        return (
            "blocked",
            None,
            "Native review requested changes; route the blocking findings to the "
            "implementation worker (do not post automatically).",
        )
    unresolved = [
        f
        for f in findings
        if isinstance(f, dict)
        and f.get("severity") == "Critical"
        and f.get("outcome") in UNRESOLVED_OUTCOMES
    ]
    if any(f.get("confidence") == "high" for f in unresolved):
        return (
            "blocked",
            None,
            "Unresolved high-confidence Critical finding; route it to the "
            "implementation worker (do not post automatically).",
        )
    if unresolved:
        return (
            "needs_human",
            None,
            "Unresolved low-confidence Critical finding; a human must verify the "
            "premise.",
        )
    if base_event == "REQUEST_CHANGES":
        return (
            "needs_human",
            None,
            "Base event was REQUEST_CHANGES but the final event was downgraded; a "
            "human must confirm the downgrade.",
        )
    if event == "APPROVE":
        return ("pass", None, "No action required.")
    if event == "COMMENT" and base_event in ("APPROVE", "COMMENT"):
        return (
            "pass",
            None,
            "No action required; informational findings are preserved in the evidence.",
        )
    return (
        "needs_human",
        None,
        "Ambiguous native outcome; a human must review the evidence.",
    )


# ---------------------------------------------------------------------------
# Evidence layout
# ---------------------------------------------------------------------------


def make_run_dir(state_root: Path, owner: str, repo: str, pr_number: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = state_root / "runs" / f"{owner}__{repo}" / f"pr-{pr_number}"
    for _ in range(5):
        candidate = base / f"{stamp}-{secrets.token_hex(4)}"
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            continue
        except OSError as exc:
            raise ReviewError(f"cannot create evidence directory: {exc}")
    raise ReviewError("cannot create a unique evidence directory")


def new_ctx(run_dir: Path, **fields) -> dict:
    ctx = {
        "runDir": run_dir,
        "repository": None,
        "prNumber": None,
        "prUrl": None,
        "invocationTransport": TRANSPORT_DIRECT,
        "requestedEffort": None,
        "expectedHead": None,
        "observedHeadAtResolve": None,
        "startedAt": None,
        "selectedEffort": None,
        "effortReasons": [],
        "reviewTimeoutMinutes": None,
        "wrapperTimeoutSeconds": None,
        "observedHeadBefore": None,
        "observedHeadAfter": None,
        "requiredChecks": None,
        "qwenVersion": None,
        "qwenExitCode": None,
        "completed": None,
        "timedOut": None,
        "event": None,
        "baseEvent": None,
        "cappedBy": None,
        "localIdentityBefore": None,
        "localIdentityAfter": None,
        "findings": [],
    }
    ctx.update(fields)
    return ctx


def write_preflight(ctx: dict, pr_doc: dict) -> None:
    view = policy_view(pr_doc)
    preflight = {
        "contractVersion": CONTRACT_VERSION,
        "effortPolicyVersion": EFFORT_POLICY_VERSION,
        "startedAt": ctx["startedAt"],
        "repository": ctx["repository"],
        "prNumber": ctx["prNumber"],
        "prUrl": ctx["prUrl"],
        "requestedEffort": ctx["requestedEffort"],
        "expectedHead": ctx["expectedHead"],
        "observedHeadAtResolve": ctx["observedHeadAtResolve"],
        "state": pr_doc.get("state"),
        "isDraft": pr_doc.get("isDraft"),
        "additions": pr_doc.get("additions"),
        "deletions": pr_doc.get("deletions"),
        "changedFiles": pr_doc.get("changedFiles"),
        "labels": view["labels"],
        "files": view["filePaths"],
        "title": pr_doc.get("title"),
    }
    atomic_write_json(ctx["runDir"] / "preflight.json", preflight)


def build_result(ctx: dict) -> dict:
    run_dir: Path = ctx["runDir"]
    artifacts = []
    hashes = {}
    for name in ("preflight.json", "qwen-run.json", "qwen-stderr.log", "review.md", "review.json"):
        path = run_dir / name
        if path.is_file():
            artifacts.append(name)
            try:
                hashes[name] = sha256_file(path)
            except OSError:
                pass
    artifacts.append("result.json")
    return {
        "contractVersion": CONTRACT_VERSION,
        "effortPolicyVersion": EFFORT_POLICY_VERSION,
        "repository": ctx["repository"],
        "prNumber": ctx["prNumber"],
        "prUrl": ctx["prUrl"],
        "reviewKey": f"{ctx['repository']}#{ctx['prNumber']}@{ctx['expectedHead']}",
        "attemptId": run_dir.name,
        "invocationTransport": ctx.get("invocationTransport") or TRANSPORT_DIRECT,
        "requestedEffort": ctx["requestedEffort"],
        "selectedEffort": ctx["selectedEffort"],
        "effortReasons": ctx["effortReasons"],
        "reviewTimeoutMinutes": ctx["reviewTimeoutMinutes"],
        "wrapperTimeoutSeconds": ctx["wrapperTimeoutSeconds"],
        "expectedHead": ctx["expectedHead"],
        "observedHeadAtResolve": ctx["observedHeadAtResolve"],
        "observedHeadBefore": ctx["observedHeadBefore"],
        "observedHeadAfter": ctx["observedHeadAfter"],
        "requiredChecks": ctx["requiredChecks"],
        "qwenVersion": ctx["qwenVersion"],
        "startedAt": ctx["startedAt"],
        "finishedAt": utc_now_iso(),
        "qwenExitCode": ctx["qwenExitCode"],
        "completed": ctx["completed"],
        "timedOut": ctx["timedOut"],
        "event": ctx["event"],
        "baseEvent": ctx["baseEvent"],
        "cappedBy": ctx["cappedBy"],
        "localIdentityBefore": ctx["localIdentityBefore"],
        "localIdentityAfter": ctx["localIdentityAfter"],
        "findings": ctx["findings"],
        "disposition": ctx.get("disposition"),
        "semanticExitCode": DISPOSITION_EXIT.get(ctx.get("disposition")),
        "error": ctx.get("error"),
        "nextAction": ctx.get("nextAction"),
        "artifacts": artifacts,
        "artifactSha256": hashes,
    }


def validate_background_result(ctx: dict, result_path: Path) -> dict:
    """Re-read result.json from disk and prove it belongs to exactly this
    invocation (contractVersion, reviewKey, attemptId, invocationTransport,
    disposition, semanticExitCode). An absent, empty, stale, or malformed
    file — or any field mismatch — is a ReviewError; transport success is
    never inferred from an old result file or loosely parsed stdout.
    """
    run_dir: Path = ctx["runDir"]
    try:
        raw = result_path.read_bytes()
    except OSError as exc:
        raise ReviewError(
            f"background envelope: result.json is absent or unreadable "
            f"({result_path}): {type(exc).__name__}: {exc}"
        )
    if not raw:
        raise ReviewError("background envelope: result.json is empty")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReviewError("background envelope: result.json is malformed")
    if not isinstance(doc, dict):
        raise ReviewError(
            "background envelope: result.json is not a JSON object"
        )
    expected = {
        "contractVersion": CONTRACT_VERSION,
        "reviewKey": (
            f"{ctx['repository']}#{ctx['prNumber']}"
            f"@{ctx['expectedHead']}"
        ),
        "attemptId": run_dir.name,
        "invocationTransport": ctx.get("invocationTransport") or TRANSPORT_DIRECT,
        "disposition": ctx["disposition"],
        "semanticExitCode": DISPOSITION_EXIT.get(ctx["disposition"]),
    }
    for field, want in expected.items():
        if doc.get(field) != want:
            raise ReviewError(
                f"envelope: result.json {field} "
                f"{doc.get(field)!r} does not match this invocation "
                f"({want!r})"
            )
    return doc


# ---------------------------------------------------------------------------
# Monitor-envelope transport (Qwen native Monitor notification stream)
# ---------------------------------------------------------------------------

# The single in-flight monitor session for this process. The helper is always
# invoked as one process per review (a Qwen Monitor child), so a module-level
# reference is safe and lets the top-level exception handlers stop the
# heartbeat and emit a terminal transport_error even when the failure occurs
# outside finish().
_MONITOR_SESSION = None


def _monitor_chat_dir(outer_session_id):
    """Resolve the exact Qwen chats directory from the outer session id."""
    if not outer_session_id or not re.fullmatch(r"[A-Za-z0-9_-]+", outer_session_id):
        return None
    root = Path.home() / ".qwen/projects"
    matches = list(root.glob(f"*/chats/{outer_session_id}.jsonl"))
    if len(matches) != 1:
        return None
    return matches[0].parent


def _credible_inner_transcript(path, toplevel):
    """Validate the immutable identity fields of a candidate chat transcript."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        first.get("type") == "user"
        and first.get("cwd") == str(toplevel)
        and first.get("sessionId") == path.stem
    )


class ReviewProgress:
    """Best-effort, model-free progress projection from one inner Qwen chat."""

    def __init__(self, toplevel, started_epoch, outer_session_id):
        self._toplevel = Path(toplevel)
        self._started_epoch = started_epoch
        self._outer_session_id = outer_session_id
        self._transcript = None
        self._offset = 0
        self._agent_calls = set()
        self._agent_results = set()
        self._last_activity = None

    def _bind_once(self):
        if self._transcript is not None:
            return True
        chats = _monitor_chat_dir(self._outer_session_id)
        if chats is None:
            return False
        candidates = []
        try:
            paths = list(chats.glob("*.jsonl"))
        except OSError:
            return False
        for path in paths:
            if path.stem == self._outer_session_id:
                continue
            try:
                if path.stat().st_mtime < self._started_epoch:
                    continue
            except OSError:
                continue
            if _credible_inner_transcript(path, self._toplevel):
                candidates.append(path)
        if len(candidates) != 1:
            return False
        self._transcript = candidates[0]
        return True

    def _consume_record(self, record):
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            self._last_activity = timestamp
        if record.get("type") == "assistant":
            message = record.get("message")
            if not isinstance(message, dict):
                return
            for part in message.get("parts", []):
                if not isinstance(part, dict):
                    continue
                call = part.get("functionCall")
                if not isinstance(call, dict) or call.get("name") != "agent":
                    continue
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    self._agent_calls.add(call_id)
        elif record.get("type") == "tool_result":
            result = record.get("toolCallResult")
            if not isinstance(result, dict):
                return
            call_id = result.get("callId")
            if isinstance(call_id, str) and call_id in self._agent_calls:
                self._agent_results.add(call_id)

    def snapshot(self):
        try:
            if not self._bind_once():
                return None
            with self._transcript.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
            consumed = 0
            for line in chunk.splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    break
                consumed += len(line)
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._consume_record(record)
            self._offset += consumed
        except (OSError, ValueError):
            return None
        started = len(self._agent_calls)
        completed = len(self._agent_results)
        if started == 0:
            stage = "preparing"
        elif completed < started:
            stage = "finder_fanout"
        else:
            stage = "post_fanout"
        result = {
            "stage": stage,
            "agentsStarted": started,
            "agentsCompleted": completed,
        }
        if self._last_activity is not None:
            result["lastActivityAt"] = self._last_activity
        return result


class MonitorSession:
    """Bounded, deterministic protocol-event emitter for the monitor transport.

    Emits single-line JSON events prefixed with AO_PR_REVIEW_EVENT=. A daemon
    thread emits transport keepalives plus less-frequent bounded progress
    heartbeats until stopped. stdout writes are serialized under a lock so the
    worker thread and the main thread never interleave a partial line. Progress
    is observational only: it never drives the review and never emits finding
    text or externally supplied prose. Terminal metadata remains authoritative.
    """

    def __init__(self, interval=None, stream=None):
        self._interval = (
            MONITOR_HEARTBEAT_SECONDS if interval is None else interval
        )
        self._keepalive_interval = (
            MONITOR_KEEPALIVE_SECONDS if interval is None else interval
        )
        self._stream = stream
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._start = None
        self._progress = None

    def configure_progress(self, toplevel, started_epoch):
        self._progress = ReviewProgress(
            toplevel, started_epoch, os.environ.get("QWEN_CODE_SESSION_ID")
        )

    # -- liveness ---------------------------------------------------------

    def start(self):
        self._start = time.monotonic()
        self._thread = threading.Thread(
            target=self._heartbeat_worker, daemon=True
        )
        self._thread.start()
        return self

    def _heartbeat_worker(self):
        # Production ticks at the transport keepalive interval. Every second
        # tick is the richer 960-second progress heartbeat. Tests that inject
        # an interval keep the historical one-event-per-tick behavior.
        next_heartbeat = self._start + self._interval
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._keepalive_interval):
                return
            now = time.monotonic()
            elapsed = int(now - self._start)
            if now < next_heartbeat:
                self.emit({"type": "keepalive", "elapsedSeconds": elapsed})
                continue
            next_heartbeat += self._interval
            event = {"type": "heartbeat", "elapsedSeconds": elapsed}
            if self._progress is not None:
                try:
                    progress = self._progress.snapshot()
                except Exception:
                    progress = None
                if progress:
                    event.update(progress)
            self.emit(event)

    # -- event emission ---------------------------------------------------

    def emit(self, event):
        line = MONITOR_EVENT_PREFIX + json.dumps(
            event, separators=(",", ":")
        )
        stream = self._stream if self._stream is not None else sys.stdout
        with self._lock:
            stream.write(line + "\n")
            stream.flush()

    # -- terminal events (heartbeats are always stopped first) ------------

    def _halt(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def stop_and_emit_complete(self, result_path, disposition,
                               semantic_code, review_key, attempt_id):
        self._halt()
        self.emit(
            {
                "type": "complete",
                "resultJson": str(result_path),
                "disposition": disposition,
                "semanticExitCode": semantic_code,
                "reviewKey": review_key,
                "attemptId": attempt_id,
            }
        )

    def stop_and_emit_error(self, semantic_code):
        self._halt()
        self.emit({"type": "transport_error", "semanticExitCode": semantic_code})


def _emit_monitor_terminal_error(semantic_code):
    """Stop the in-flight monitor session (if any) and emit transport_error.

    Safe to call from the top-level exception handlers: a no-op when no
    monitor session is active (e.g. a failure before the session was created).
    """
    global _MONITOR_SESSION
    if _MONITOR_SESSION is not None:
        _MONITOR_SESSION.stop_and_emit_error(semantic_code)
        _MONITOR_SESSION = None


def finish(ctx: dict, disposition: str, error: str = None, next_action: str = None) -> int:
    global _MONITOR_SESSION
    ctx["disposition"] = disposition
    ctx["error"] = error
    ctx["nextAction"] = next_action
    result_path = ctx["runDir"] / "result.json"
    session = ctx.get("monitorSink")
    try:
        atomic_write_json(result_path, build_result(ctx))
    except Exception:
        # No trustworthy result.json may ever carry the original
        # disposition into stdout or the exit code. Ordinary
        # persistence/serialization failures only (not BaseException).
        if session is not None:
            session.stop_and_emit_error(EXIT_REVIEW_ERROR)
            _MONITOR_SESSION = None
            return EXIT_REVIEW_ERROR
        print(
            "review_error: failed to persist result.json in evidence "
            f"directory {ctx['runDir']}"
        )
        print("DISPOSITION=review_error")
        print(f"SEMANTIC_EXIT_CODE={EXIT_REVIEW_ERROR}")
        print("RESULT_JSON=")
        return EXIT_REVIEW_ERROR
    semantic_code = DISPOSITION_EXIT[disposition]

    if session is not None:
        # The monitor transport may only emit `complete` after the persisted
        # result has been re-read from disk and proven to belong to exactly
        # this invocation. Transport exit 0 means "a trustworthy result
        # exists", never "the review passed". No human-readable progress or
        # finding prose is ever emitted — only the bounded protocol event.
        try:
            validate_background_result(ctx, result_path)
        except ReviewError:
            session.stop_and_emit_error(EXIT_REVIEW_ERROR)
            _MONITOR_SESSION = None
            return EXIT_REVIEW_ERROR
        session.stop_and_emit_complete(
            result_path,
            disposition,
            semantic_code,
            f"{ctx['repository']}#{ctx['prNumber']}@{ctx['expectedHead']}",
            ctx["runDir"].name,
        )
        _MONITOR_SESSION = None
        return EXIT_PASS

    line = (
        f"ao-pr-review: {DISPOSITION_LABEL[disposition]} "
        f"PR #{ctx['prNumber']} {ctx['prUrl']}"
    )
    if error:
        line += f" - {error}"
    print(line)
    if ctx.get("invocationTransport") == TRANSPORT_BACKGROUND_ENVELOPE:
        # The background transport may only succeed after the persisted
        # result has been re-read from disk and proven to belong to
        # exactly this invocation. Transport exit 0 means "a trustworthy
        # result exists", never "the review passed".
        try:
            validate_background_result(ctx, result_path)
        except ReviewError as exc:
            print(f"review_error: {exc}")
            print("DISPOSITION=review_error")
            print(f"SEMANTIC_EXIT_CODE={EXIT_REVIEW_ERROR}")
            print("RESULT_JSON=")
            return EXIT_REVIEW_ERROR
        exit_code = EXIT_PASS
    else:
        exit_code = semantic_code
    print(f"DISPOSITION={disposition}")
    print(f"SEMANTIC_EXIT_CODE={semantic_code}")
    print(f"RESULT_JSON={result_path}")
    return exit_code


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def run_review(tokens: list, transport: str = TRANSPORT_DIRECT, session=None) -> int:
    target = tokens[0]
    expected_head = tokens[1]
    requested_effort = tokens[2] if len(tokens) == 3 else "auto"

    if requested_effort not in ("auto", "medium", "high"):
        raise UsageError(
            f"invalid effort {requested_effort!r} (expected auto, medium, or high)"
        )
    if not SHA_RE.match(expected_head):
        raise UsageError(
            "expected head must be exactly 40 lowercase hexadecimal characters"
        )
    if target.isdigit():
        if int(target) <= 0:
            raise UsageError("PR number must be a positive integer")
    elif not GITHUB_URL_RE.match(target):
        raise UsageError("target must be a PR number or a GitHub pull request URL")

    started_at = utc_now_iso()
    state_root = Path(os.environ.get(STATE_ROOT_ENV) or DEFAULT_STATE_ROOT)

    current_repo = resolve_current_repo()

    pr_doc = gh_json(["pr", "view", target, "--json", PR_VIEW_FIELDS], "resolve PR")
    canonical = pr_doc.get("url") if isinstance(pr_doc, dict) else None
    match = PR_URL_RE.match(canonical) if isinstance(canonical, str) else None
    if not match:
        raise ReviewError(
            f"resolved PR URL is not in canonical GitHub shape: {canonical!r}"
        )
    pr_owner, pr_repo = match.group(1), match.group(2)
    pr_number = int(match.group(3))
    base_repo = f"{pr_owner}/{pr_repo}"

    if base_repo != current_repo:
        run_dir = make_run_dir(state_root, pr_owner, pr_repo, pr_number)
        ctx = new_ctx(
            run_dir,
            repository=base_repo,
            prNumber=pr_number,
            prUrl=canonical,
            invocationTransport=transport,
            monitorSink=session,
            requestedEffort=requested_effort,
            expectedHead=expected_head,
            observedHeadAtResolve=pr_doc.get("headRefOid"),
            startedAt=started_at,
        )
        write_preflight(ctx, pr_doc)
        return finish(
            ctx,
            "review_error",
            error=(
                f"resolved PR base repository {base_repo} differs from current "
                f"repository {current_repo}"
            ),
            next_action=(
                f"Run this review from an orchestrator session in {base_repo} "
                "and re-run /ao-pr-review there."
            ),
        )

    run_dir = make_run_dir(state_root, pr_owner, pr_repo, pr_number)
    ctx = new_ctx(
        run_dir,
        repository=base_repo,
        prNumber=pr_number,
        prUrl=canonical,
        invocationTransport=transport,
        monitorSink=session,
        requestedEffort=requested_effort,
        expectedHead=expected_head,
        observedHeadAtResolve=pr_doc.get("headRefOid"),
        startedAt=started_at,
    )
    write_preflight(ctx, pr_doc)

    # Preconditions 2-4.
    if pr_doc.get("state") != "OPEN":
        return finish(
            ctx,
            "review_error",
            error=f"PR is not OPEN (state={pr_doc.get('state')!r})",
            next_action=EARLY_NEXT_ACTION,
        )
    if pr_doc.get("isDraft") is not False:
        return finish(
            ctx,
            "review_error",
            error="PR is a draft; a non-draft PR is required",
            next_action=EARLY_NEXT_ACTION,
        )
    if pr_doc.get("headRefOid") != expected_head:
        return finish(
            ctx,
            "review_error",
            error=(
                f"resolved head {pr_doc.get('headRefOid')!r} does not match "
                f"expected head {expected_head!r}"
            ),
            next_action="Re-run with the PR's current head SHA.",
        )

    # Preconditions 5-8: required DETERMINISTIC CI must exist and all pass.
    # The full snapshot is preserved as evidence; the decision uses only the
    # structured check list. The exact commit-status context
    # SEMANTIC_REVIEW_CONTEXT is excluded from the prerequisites — the
    # orchestrator publishes it only after a semantic review, so it can
    # never gate the review that is the only thing able to pass it.
    # Similarly named contexts are never excluded (exact match only).
    snapshot = take_checks_snapshot(canonical)
    ctx["requiredChecks"] = snapshot
    if snapshot.get("parseError"):
        return finish(
            ctx,
            "review_error",
            error=(
                "required checks snapshot is unusable: "
                f"{snapshot['parseError']} (gh pr checks exit "
                f"{snapshot['exitCode']})"
            ),
            next_action=EARLY_NEXT_ACTION,
        )
    deterministic = [
        c for c in snapshot["checks"]
        if not (
            isinstance(c, dict)
            and c.get("name") == SEMANTIC_REVIEW_CONTEXT
        )
    ]
    if not deterministic:
        return finish(
            ctx,
            "review_error",
            error=(
                "no required deterministic checks were reported: either "
                "no required checks at all, or the only reported required "
                "check is the excluded 'ao/semantic-review' context"
            ),
            next_action=EARLY_NEXT_ACTION,
        )
    failing = [
        c for c in deterministic
        if not (isinstance(c, dict) and c.get("bucket") == "pass")
    ]
    if failing:
        return finish(
            ctx,
            "review_error",
            error="required deterministic checks not all passing: "
            + json.dumps(failing, sort_keys=True),
            next_action="Wait for the required deterministic checks to pass, then re-run.",
        )

    # Precondition 9: initial head re-read after the CI checks (evidence and
    # early refusal). The final pre-review head read happens later, after
    # all preparation, immediately before the single qwen review run.
    head_before = read_pr_head(canonical)
    ctx["observedHeadBefore"] = head_before
    if head_before is None:
        return finish(
            ctx,
            "review_error",
            error="pre-review head read returned an invalid headRefOid",
            next_action=EARLY_NEXT_ACTION,
        )
    if head_before != expected_head:
        return finish(
            ctx,
            "review_error",
            error=(
                f"PR head moved after CI checks ({head_before} != {expected_head})"
            ),
            next_action="Re-run with the PR's current head SHA.",
        )

    # Deterministic effort selection (bounded patch inspection included).
    # Repository-specific risk extensions are optional, but when present they
    # are schema-validated and fail closed before Qwen inference.
    toplevel = repo_toplevel()
    try:
        project_risk, project_risk_meta = load_project_risk_config(toplevel)
    except (OSError, ValueError) as exc:
        return finish(
            ctx,
            "review_error",
            error=f"invalid project review risk configuration: {exc}",
            next_action=(
                "Fix .qwen/review-config.json or remove it if no project-specific "
                "risk extensions are required, then re-run."
            ),
        )
    ctx["projectRiskConfig"] = project_risk_meta
    patch, patch_error = fetch_patch(canonical)
    selected_effort, effort_reasons = select_effort(
        requested_effort, policy_view(pr_doc), patch, patch_error, project_risk
    )
    ctx["selectedEffort"] = selected_effort
    ctx["effortReasons"] = effort_reasons

    # Repository guard baseline: the repository must have no tracked or
    # staged changes before Qwen launches. A pre-dirty tracked file could
    # change again without altering its porcelain status line, so a dirty
    # baseline is refused instead of diffed against the post-review state.
    git_before = tracked_status(toplevel)
    if git_before:
        return finish(
            ctx,
            "review_error",
            error=(
                "orchestrator repository has tracked/staged changes before "
                "the review: " + json.dumps(git_before, sort_keys=True)
            ),
            next_action=(
                "Commit or revert the tracked/staged changes and re-run; "
                "Qwen was not launched."
            ),
        )

    # Precondition 10: per-base-repository/per-PR lock, non-blocking.
    lock_dir = state_root / "locks" / f"{pr_owner}__{pr_repo}"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"pr-{pr_number}.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    locked = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            return finish(
                ctx,
                "review_error",
                error=(
                    "another review is already in progress for this PR (lock "
                    "held); Qwen was not launched"
                ),
                next_action="Wait for the other review to finish, then re-run.",
            )

        # qwen --version once, for evidence only (not a semantic review).
        try:
            version_proc = subprocess.run(
                ["qwen", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=toplevel,
                env=os.environ,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            return finish(
                ctx,
                "review_error",
                error=f"qwen executable unavailable: {exc}",
                next_action=EARLY_NEXT_ACTION,
            )
        if version_proc.returncode != 0:
            return finish(
                ctx,
                "review_error",
                error=f"qwen --version exited {version_proc.returncode}",
                next_action=EARLY_NEXT_ACTION,
            )
        version_text = (version_proc.stdout or "").strip()
        ctx["qwenVersion"] = version_text.splitlines()[0] if version_text else None

        # Precondition 11: local repository identity snapshot, taken under
        # the lock, after qwen --version, and before the final remote
        # PR-head read. Read-only git commands only (status, rev-parse,
        # symbolic-ref). A clean status is required; the local HEAD commit
        # and the symbolic branch/ref are recorded so that a child process
        # which commits its changes and leaves a clean worktree is still
        # detected after the review.
        try:
            identity_before = read_local_identity(toplevel)
        except ReviewError as exc:
            return finish(
                ctx,
                "review_error",
                error=f"local repository identity read failed before launch: {exc}",
                next_action=EARLY_NEXT_ACTION,
            )
        if identity_before["trackedStatus"]:
            return finish(
                ctx,
                "review_error",
                error=(
                    "orchestrator repository has tracked/staged changes "
                    "after preparation: "
                    + json.dumps(identity_before["trackedStatus"], sort_keys=True)
                ),
                next_action=(
                    "Commit or revert the tracked/staged changes and re-run; "
                    "Qwen was not launched."
                ),
            )
        ctx["localIdentityBefore"] = identity_before

        # Precondition 12: final pre-review head read. It is the last
        # external read before launch and must be valid and exactly equal
        # to the expected SHA; the next external operation after it
        # succeeds is the single qwen review run. Head movement during
        # patch inspection or other preparation is refused before Qwen.
        head_final = None
        try:
            head_final = read_pr_head(canonical)
        except ReviewError:
            head_final = None
        if head_final is None:
            return finish(
                ctx,
                "review_error",
                error="final pre-review head read returned an invalid headRefOid",
                next_action=EARLY_NEXT_ACTION,
            )
        ctx["observedHeadBefore"] = head_final
        if head_final != expected_head:
            return finish(
                ctx,
                "review_error",
                error=(
                    f"PR head moved after preparation ({head_final} != "
                    f"{expected_head}); Qwen was not launched"
                ),
                next_action="Re-run with the PR's current head SHA.",
            )

        # Exactly one native semantic review. The native timeout is the
        # deterministic effort-based budget (medium 240 / high 480
        # minutes); the wrapper timeout adds 600 seconds of cleanup grace
        # beyond that budget.
        timeout_minutes, wrapper_timeout_seconds = native_timeout_plan(
            selected_effort
        )
        ctx["reviewTimeoutMinutes"] = timeout_minutes
        ctx["wrapperTimeoutSeconds"] = wrapper_timeout_seconds

        command = [
            "qwen", "review", "run", canonical,
            "--effort", selected_effort,
            "--json",
            "--fail-on", "request-changes",
            "--approval-mode", "yolo",
            "--timeout-minutes", str(timeout_minutes),
        ]
        native_review_started = time.time()
        if session is not None:
            session.configure_progress(toplevel, native_review_started)
        try:
            review_proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=wrapper_timeout_seconds,
                cwd=toplevel,
                env=os.environ,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            return finish(
                ctx,
                "review_error",
                error=f"qwen subprocess failure: {exc}",
                next_action=EARLY_NEXT_ACTION,
            )

        # Preserve raw artifacts unconditionally.
        try:
            atomic_write_bytes(
                run_dir / "qwen-run.json",
                (review_proc.stdout or "").encode("utf-8"),
            )
            atomic_write_bytes(
                run_dir / "qwen-stderr.log",
                (review_proc.stderr or "").encode("utf-8"),
            )
        except OSError as exc:
            return finish(
                ctx,
                "review_error",
                error=f"failed to preserve raw qwen artifacts: {exc}",
                next_action=EARLY_NEXT_ACTION,
            )

        # Repository guard after the review: tracked/staged status, the
        # local HEAD, and the symbolic branch/ref are re-read immediately
        # after the native run. Any tracked/staged entry, local HEAD
        # change, or branch/ref change is review_error; nothing is
        # repaired or reverted. This also catches a child process that
        # commits its changes and leaves a clean worktree.
        identity_after = None
        identity_error = None
        try:
            identity_after = read_local_identity(toplevel)
        except ReviewError as exc:
            identity_error = (
                "local repository identity read failed after the review: "
                f"{exc}"
            )
        if identity_error is None and identity_after["trackedStatus"]:
            # The baseline was required to be clean, so any tracked/staged
            # entry after the review is a change made during it.
            identity_error = (
                "tracked repository content changed during review: "
                + json.dumps(identity_after["trackedStatus"], sort_keys=True)
            )
        elif (
            identity_error is None
            and identity_after["head"] != identity_before["head"]
        ):
            identity_error = (
                "local repository HEAD changed during review "
                f"({identity_before['head']} -> {identity_after['head']})"
            )
        elif (
            identity_error is None
            and identity_after["ref"] != identity_before["ref"]
        ):
            identity_error = (
                "local repository branch/ref changed during review "
                f"({identity_before['ref']!r} -> {identity_after['ref']!r})"
            )
        ctx["localIdentityAfter"] = identity_after

        # Strict Qwen 0.22.3 result validation.
        validation_errors = []
        ctx["qwenExitCode"] = review_proc.returncode
        if review_proc.returncode not in (0, 3):
            validation_errors.append(
                f"qwen exit {review_proc.returncode} is not an expected completed "
                "outcome (0 or 3)"
            )

        wrapper = extract_wrapper(review_proc.stdout)
        if wrapper is None:
            validation_errors.append(
                "no wrapper JSON object found in the current qwen stdout"
            )
        else:
            validation_errors.extend(validate_wrapper(wrapper))
            if isinstance(wrapper.get("completed"), bool):
                ctx["completed"] = wrapper["completed"]
            if isinstance(wrapper.get("timedOut"), bool):
                ctx["timedOut"] = wrapper["timedOut"]
            report_path = wrapper.get("reportPath")
            if isinstance(report_path, str) and report_path:
                report_src = Path(report_path)
                if report_src.is_file():
                    try:
                        copy_bytes(report_src, run_dir / "review.md")
                    except OSError as exc:
                        validation_errors.append(f"failed to copy report: {exc}")
                else:
                    validation_errors.append(
                        f"reportPath file not found: {report_path}"
                    )
                if report_path.endswith(".md"):
                    companion_src = Path(report_path[: -len(".md")] + ".json")
                    if companion_src.is_file():
                        try:
                            copy_bytes(companion_src, run_dir / "review.json")
                        except OSError as exc:
                            validation_errors.append(f"failed to copy companion: {exc}")
                    else:
                        validation_errors.append(
                            f"companion file not found: {companion_src}"
                        )

        companion = None
        companion_file = run_dir / "review.json"
        if companion_file.is_file():
            try:
                companion = json.loads(companion_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                validation_errors.append("companion review.json is not valid JSON")
        if companion is not None:
            validation_errors.extend(
                validate_companion(companion, pr_number, selected_effort)
            )
            verdict = companion.get("verdict")
            if isinstance(verdict, dict):
                event = verdict.get("event")
                base_event = verdict.get("baseEvent")
                capped_by = verdict.get("cappedBy")
                if event in ALLOWED_EVENTS:
                    ctx["event"] = event
                if base_event in ALLOWED_EVENTS:
                    ctx["baseEvent"] = base_event
                if (
                    isinstance(capped_by, list)
                    and all(isinstance(x, str) for x in capped_by)
                ):
                    ctx["cappedBy"] = capped_by
                if wrapper is not None:
                    for field in ("event", "baseEvent"):
                        wrapper_value = wrapper.get(field)
                        companion_value = verdict.get(field)
                        if (
                            wrapper_value in ALLOWED_EVENTS
                            and companion_value in ALLOWED_EVENTS
                            and wrapper_value != companion_value
                        ):
                            validation_errors.append(
                                f"{field} differs between wrapper "
                                f"({wrapper_value!r}) and companion verdict "
                                f"({companion_value!r})"
                            )
                    wrapper_capped = wrapper.get("cappedBy")
                    if (
                        isinstance(wrapper_capped, list)
                        and all(isinstance(x, str) for x in wrapper_capped)
                        and isinstance(capped_by, list)
                        and all(isinstance(x, str) for x in capped_by)
                        and wrapper_capped != capped_by
                    ):
                        validation_errors.append(
                            f"cappedBy differs between wrapper "
                            f"({wrapper_capped!r}) and companion verdict "
                            f"({capped_by!r})"
                        )
        if ctx["event"] in ALLOWED_EVENTS:
            if review_proc.returncode == 3 and ctx["event"] != "REQUEST_CHANGES":
                validation_errors.append(
                    f"qwen exit 3 but event is {ctx['event']} "
                    "(expected REQUEST_CHANGES)"
                )
            if ctx["event"] == "REQUEST_CHANGES" and review_proc.returncode != 3:
                validation_errors.append(
                    f"event is REQUEST_CHANGES but qwen exit is "
                    f"{review_proc.returncode} (expected 3)"
                )

        findings = None
        if isinstance(companion, dict) and isinstance(companion.get("findings"), list):
            findings = companion["findings"]
            validation_errors.extend(validate_findings(findings))
        ctx["findings"] = findings if isinstance(findings, list) else []

        # Post-review head re-read (never reuse an earlier value).
        head_after = None
        head_read_failed = False
        try:
            head_after = read_pr_head(canonical)
        except ReviewError:
            head_read_failed = True
        ctx["observedHeadAfter"] = head_after

        run_ok = not validation_errors
        disposition, disposition_error, next_action = compute_disposition(
            expected=expected_head,
            head_after=head_after,
            head_read_failed=head_read_failed,
            run_ok=run_ok,
            qwen_exit=review_proc.returncode,
            event=ctx["event"],
            base_event=ctx["baseEvent"],
            findings=ctx["findings"],
        )
        error = disposition_error
        if identity_error:
            disposition = "review_error"
            error = identity_error
            next_action = (
                "Local repository identity changed during review; do not "
                "attempt automatic repair. Inspect the repository and the "
                "evidence."
            )
        elif disposition == "review_error" and error is None:
            error = (
                "; ".join(validation_errors)
                if validation_errors
                else "native run failed validation"
            )
        return finish(ctx, disposition, error=error, next_action=next_action)
    finally:
        if locked:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        elif not os.get_inheritable(lock_fd):
            os.close(lock_fd)


def main(argv: list) -> int:
    global _MONITOR_SESSION
    monitor_envelope = False
    background_envelope = False
    try:
        if "--monitor-envelope" in argv:
            if argv.count("--monitor-envelope") > 1:
                print(
                    "usage error: --monitor-envelope may be given at most once",
                    file=sys.stderr,
                )
                print(USAGE, file=sys.stderr)
                return EXIT_USAGE
            monitor_envelope = True
            argv = [a for a in argv if a != "--monitor-envelope"]
        if "--background-envelope" in argv:
            if argv.count("--background-envelope") > 1:
                print(
                    "usage error: --background-envelope may be given at most once",
                    file=sys.stderr,
                )
                print(USAGE, file=sys.stderr)
                return EXIT_USAGE
            background_envelope = True
            argv = [a for a in argv if a != "--background-envelope"]
        if monitor_envelope and background_envelope:
            print(
                "usage error: --monitor-envelope and --background-envelope "
                "are mutually exclusive",
                file=sys.stderr,
            )
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE
        if argv in (["help"], ["-h"], ["--help"]):
            print(USAGE)
            return EXIT_PASS
        tokens = None
        if argv and argv[0] == "--args-file":
            if len(argv) != 2:
                print("usage error: --args-file requires exactly one path", file=sys.stderr)
                print(USAGE, file=sys.stderr)
                return EXIT_USAGE
            tokens = read_args_file(argv[1])
        elif len(argv) in (2, 3) and not argv[0].startswith("-"):
            tokens = list(argv)
        else:
            print(
                f"usage error: unsupported invocation (expected help, --args-file, "
                f"or 2-3 positional arguments, optionally prefixed with "
                f"--background-envelope or --monitor-envelope)",
                file=sys.stderr,
            )
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE
        if tokens == ["help"]:
            print(USAGE)
            return EXIT_PASS
        if len(tokens) in (2, 3):
            if monitor_envelope:
                transport = TRANSPORT_MONITOR_ENVELOPE
            elif background_envelope:
                transport = TRANSPORT_BACKGROUND_ENVELOPE
            else:
                transport = TRANSPORT_DIRECT
            session = None
            if transport == TRANSPORT_MONITOR_ENVELOPE:
                # One in-flight session per process; the monitor event thread runs
                # for the whole review and is stopped before the terminal
                # event is emitted (in finish()) or by an exception handler.
                session = MonitorSession().start()
                _MONITOR_SESSION = session
            return run_review(tokens, transport=transport, session=session)
        print(
            f"usage error: unsupported number of arguments: {len(tokens)} "
            "(expected 'help' or 2-3 review arguments)",
            file=sys.stderr,
        )
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        if monitor_envelope:
            _emit_monitor_terminal_error(EXIT_USAGE)
        return EXIT_USAGE
    except ReviewError as exc:
        if monitor_envelope:
            # Monitor stdout carries only protocol events, never prose.
            _emit_monitor_terminal_error(EXIT_REVIEW_ERROR)
            return EXIT_REVIEW_ERROR
        print(f"review_error: {exc}")
        print("DISPOSITION=review_error")
        print(f"SEMANTIC_EXIT_CODE={EXIT_REVIEW_ERROR}")
        print("RESULT_JSON=")
        return EXIT_REVIEW_ERROR
    except KeyboardInterrupt:
        # Cancellation or signal termination never carries a verdict.
        if monitor_envelope:
            _emit_monitor_terminal_error(EXIT_REVIEW_ERROR)
            return EXIT_REVIEW_ERROR
        print("review_error: interrupted; no valid verdict")
        print("DISPOSITION=review_error")
        print(f"SEMANTIC_EXIT_CODE={EXIT_REVIEW_ERROR}")
        print("RESULT_JSON=")
        return EXIT_REVIEW_ERROR
    except Exception as exc:  # fail closed; never emit a bare traceback
        if monitor_envelope:
            _emit_monitor_terminal_error(EXIT_REVIEW_ERROR)
            return EXIT_REVIEW_ERROR
        print(f"review_error: unexpected failure: {type(exc).__name__}: {exc}")
        print("DISPOSITION=review_error")
        print(f"SEMANTIC_EXIT_CODE={EXIT_REVIEW_ERROR}")
        print("RESULT_JSON=")
        return EXIT_REVIEW_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
