#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

PROJECT_FILES = (
    "QWEN.md",
    "PROJECT.md",
    "ARCHITECTURE.md",
    ".agent-harness.json",
    ".qwen/review-rules.md",
    ".qwen/review-config.json",
    ".github/workflows/verify.yml",
    "scripts/verify",
    ".gitignore",
    "docs/ao/github-semantic-review-publication.md",
)


def run(cmd: list[str], *, check=True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): "
            + " ".join(shlex.quote(x) for x in cmd)
            + "\n" + (proc.stderr or proc.stdout).strip()
        )
    return proc

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_project(project_id: str) -> dict:
    proc = run(["ao", "project", "get", project_id, "--json"], check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"cannot read AO project {project_id!r}: "
            + (proc.stderr or proc.stdout).strip()
        )
    doc = json.loads(proc.stdout)
    project = doc.get("project")
    if not isinstance(project, dict) or project.get("id") != project_id:
        raise RuntimeError("AO project response is malformed or identity-mismatched")
    return project


def active_sessions() -> list[dict]:
    proc = run(["ao", "session", "ls", "--all", "--include-terminated", "--json"])
    doc = json.loads(proc.stdout)
    data = doc.get("data")
    if not isinstance(data, list):
        raise RuntimeError("AO session list response is malformed")
    return [item for item in data if isinstance(item, dict) and item.get("isTerminated") is False]

def repository_status(repo: Path) -> list[str]:
    output = run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"]
    ).stdout.splitlines()
    return [line for line in output if line.strip()]


def migration_safe_status(lines: list[str]) -> bool:
    for line in lines:
        path = line[3:] if len(line) >= 4 else line
        if path.startswith(".qwen/reviews/"):
            continue
        return False
    return True


def target_state_root(home: Path) -> Path:
    if home == Path.home().resolve() and os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]).expanduser().resolve()
    return home / ".local/state"


def backup(project: dict, repo: Path, home: Path) -> Path:
    project_id = project.get("id")
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise RuntimeError("AO project id is unsafe for migration state paths")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    root = target_state_root(home) / "ao-qwen-code-harness/migrations" / project_id / stamp
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    (root / "ao-project-before.json").write_text(
        json.dumps({"status": "ok", "project": project}, indent=2) + "\n"
    )
    (root / "repo-head.txt").write_text(
        run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip() + "\n"
    )
    (root / "repo-status.txt").write_text("\n".join(repository_status(repo)) + "\n")

    manifest = {"schemaVersion": 1, "files": {}}
    roots = {
        "host/.qwen/QWEN.md": home / ".qwen/QWEN.md",
        "host/.ao/rules/agentRules.md": home / ".ao/rules/agentRules.md",
        "host/.ao/rules/orchestratorRules.md": home / ".ao/rules/orchestratorRules.md",
        "host/.ao/policies/semanticReviewPublication.md": home / ".ao/policies/semanticReviewPublication.md",
        "host/.local/bin/ao-refresh-orchestrator": home / ".local/bin/ao-refresh-orchestrator",
    }
    expected_skill = ROOT / "global/qwen/skills/ao-pr-review"
    for source in sorted(expected_skill.rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts and source.suffix not in {".pyc", ".pyo"}:
            rel = source.relative_to(expected_skill)
            target = home / ".qwen/skills/ao-pr-review" / rel
            roots[f"host/{target.relative_to(home)}"] = target
    for source in sorted((home / ".qwen/skills/ao-pr-review").rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            rel = source.relative_to(home)
            roots[f"host/{rel}"] = source
    install_manifest = target_state_root(home) / "ao-qwen-code-harness/install-manifest.json"
    roots["state/install-manifest.json"] = install_manifest
    for rel in PROJECT_FILES:
        roots[f"project/{rel}"] = repo / rel

    for archive_rel, source in roots.items():
        if source.is_file() and not source.is_symlink():
            destination = root / archive_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            manifest["files"][archive_rel] = {
                "source": str(source), "sha256": sha256(source)
            }
        elif source.exists():
            manifest["files"][archive_rel] = {
                "source": str(source), "unsupportedType": True
            }
        else:
            manifest["files"][archive_rel] = {
                "source": str(source), "present": False
            }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return root

def restore_host_snapshot(snapshot: Path, project_id: str) -> None:
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("files"), dict):
        raise RuntimeError("migration backup manifest is malformed")
    errors = []
    for archive_rel, entry in manifest["files"].items():
        if not (archive_rel.startswith("host/") or archive_rel.startswith("state/")):
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
            errors.append(f"invalid backup entry: {archive_rel}")
            continue
        target = Path(entry["source"])
        try:
            if "sha256" in entry:
                archived = snapshot / archive_rel
                if not archived.is_file() or sha256(archived) != entry["sha256"]:
                    raise RuntimeError("archived file missing or hash-mismatched")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archived, target)
            elif entry.get("present") is False:
                if target.exists():
                    if target.is_file() and not target.is_symlink():
                        target.unlink()
                    else:
                        raise RuntimeError("refusing to remove non-regular rollback target")
            elif entry.get("unsupportedType"):
                raise RuntimeError("original target had unsupported filesystem type")
        except Exception as exc:
            errors.append(f"{target}: {exc}")

    before = json.loads((snapshot / "ao-project-before.json").read_text()).get("project")
    if not isinstance(before, dict) or before.get("id") != project_id:
        errors.append("AO project backup identity is malformed")
    else:
        try:
            original_config = before.get("config") or {}
            proc = run([
                "ao", "project", "set-config", project_id,
                "--config-json", json.dumps(original_config, separators=(",", ":")),
                "--json",
            ])
            restored = json.loads(proc.stdout).get("project")
            if not isinstance(restored, dict) or restored.get("config") != original_config:
                raise RuntimeError("AO did not restore the original project config exactly")
        except Exception as exc:
            errors.append(f"AO config restore: {exc}")

    if errors:
        raise RuntimeError("rollback incomplete:\n  - " + "\n  - ".join(errors))


def merged_config(project: dict, home: Path) -> dict:
    config = json.loads(json.dumps(project.get("config") or {}))
    config["agentRules"] = (
        f"Before any task action, read and follow the file at "
        f"`{home / '.ao/rules/agentRules.md'}`."
    )
    config["orchestratorRules"] = (
        f"Before any coordination action, read and follow the file at "
        f"`{home / '.ao/rules/orchestratorRules.md'}`."
    )
    config.setdefault("worker", {})["agent"] = "qwen"
    config.setdefault("worker", {}).setdefault("agentConfig", {})
    config.setdefault("orchestrator", {})["agent"] = "qwen"
    config.setdefault("orchestrator", {}).setdefault("agentConfig", {})
    refresh = f"python3 {shlex.quote(str(home / '.local/bin/ao-refresh-orchestrator'))} run"
    post_create = config.get("postCreate")
    if not isinstance(post_create, list):
        post_create = []
    post_create = [
        item for item in post_create
        if not (isinstance(item, str) and "ao-refresh-orchestrator" in item)
    ]
    post_create.append(refresh)
    config["postCreate"] = post_create
    return config


def apply_globals(home: Path) -> None:
    run([
        "bash", str(ROOT / "install/install-host.sh"),
        "--home", str(home), "--replace",
    ])
    run([
        "bash", str(ROOT / "install/verify-install.sh"),
        "--home", str(home),
    ])

def apply_project_config(project: dict, home: Path) -> dict:
    config = merged_config(project, home)
    proc = run([
        "ao", "project", "set-config", project["id"],
        "--config-json", json.dumps(config, separators=(",", ":")),
        "--json",
    ])
    updated = json.loads(proc.stdout).get("project")
    if not isinstance(updated, dict):
        raise RuntimeError("AO set-config returned malformed project JSON")
    actual = updated.get("config")
    if actual != config:
        raise RuntimeError("AO stored config does not match the merged migration config")
    return updated


def print_preflight(project: dict, repo: Path, sessions: list[dict], status: list[str]) -> None:
    print(f"Project: {project['id']}")
    print(f"Repository: {repo}")
    print(f"Default branch: {project.get('defaultBranch')}")
    print(f"Active AO sessions: {len(sessions)}")
    for session in sessions:
        print(
            f"  - {session.get('id')} role={session.get('role')} "
            f"project={session.get('projectId')} status={session.get('status')}"
        )
    print(f"Repository status entries: {len(status)}")
    for line in status:
        print(f"  - {line}")
    print(f"Repository migration-safe: {'yes' if migration_safe_status(status) else 'no'}")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight, back up, and perform the host/AO-config portion of an existing-project harness migration."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--backup-only", action="store_true")
    parser.add_argument("--apply-host", action="store_true")
    args = parser.parse_args()

    if args.backup_only and args.apply_host:
        print("migrate-project: choose either --backup-only or --apply-host", file=sys.stderr)
        return 2

    try:
        repo = args.repo.expanduser().resolve()
        home = args.home.expanduser().resolve()
        project = get_project(args.project_id)
        project_path = Path(project.get("path", "")).expanduser().resolve()
        if project_path != repo:
            raise RuntimeError(
                f"AO project path does not match --repo: {project_path} != {repo}"
            )
        git_top = Path(
            run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"])
            .stdout.strip()
        ).resolve()
        if git_top != repo:
            raise RuntimeError("--repo must be the repository root")

        sessions = active_sessions()
        status = repository_status(repo)
        print_preflight(project, repo, sessions, status)

        if args.backup_only:
            snapshot = backup(project, repo, home)
            print(f"Backup created: {snapshot}")
            return 0

        if not args.apply_host:
            return 0

        if sessions:
            raise RuntimeError(
                "host-global cutover requires zero nonterminated AO sessions across all projects"
            )
        if not migration_safe_status(status):
            raise RuntimeError(
                "repository has changes outside generated .qwen/reviews artifacts"
            )

        snapshot = backup(project, repo, home)
        print(f"Backup created: {snapshot}")
        try:
            apply_globals(home)
            updated = apply_project_config(project, home)
        except Exception as cutover_error:
            try:
                restore_host_snapshot(snapshot, project["id"])
            except Exception as rollback_error:
                raise RuntimeError(
                    f"cutover failed: {cutover_error}; rollback also failed: {rollback_error}"
                ) from cutover_error
            raise RuntimeError(
                f"cutover failed and was rolled back: {cutover_error}"
            ) from cutover_error
        print(f"Host globals and AO config migrated for {updated['id']}")
        print("Project contract-file migration is a separate explicit step.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"migrate-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
