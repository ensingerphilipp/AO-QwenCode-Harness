#!/usr/bin/env python3
"""Onboard a Git repository before or after AO GUI registration."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

import init_project as baseline
from verify_install import project_config_failures

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=60)
    if result.returncode:
        # AO errors can contain config values. Do not echo command arguments or payloads.
        raise RuntimeError(f"{' '.join(argv[:3])} failed (exit {result.returncode}); inspect that operation before retrying")
    return result.stdout


def git(path, *args):
    return run(["git", "-C", str(path), *args]).strip()


def identity(path):
    root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    git(root, "rev-parse", "--verify", "HEAD^{commit}")
    first = git(root, "worktree", "list", "--porcelain").splitlines()[0]
    if not first.startswith("worktree "):
        raise RuntimeError("cannot locate the primary Git checkout")
    primary = Path(first[len("worktree "):]).resolve()
    if any(c in str(root) + str(primary) for c in "\n\r\0"):
        raise RuntimeError("repository paths must not contain control characters")
    return root, common, primary


def get_project(project_id):
    doc = json.loads(run(["ao", "project", "get", project_id, "--json"]))
    project = doc.get("project")
    if not isinstance(project, dict) or project.get("id") != project_id or not isinstance(project.get("config"), dict):
        raise RuntimeError("AO returned an invalid project record")
    return project


def resolve(repo, requested=None):
    root, common, primary = identity(repo)
    rows = json.loads(run(["ao", "project", "ls", "--json"])).get("projects")
    if not isinstance(rows, list):
        raise RuntimeError("AO project listing is unavailable or malformed; no registration attempted")
    matches = []
    unresolved_paths = []
    id_owner = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise RuntimeError("AO project listing contains an invalid identity")
        project = get_project(row["id"])
        if project["id"] == requested:
            id_owner = project
        if project.get("kind") == "cloud":
            continue
        if not project.get("path"):
            raise RuntimeError("a local AO project has no repository path")
        try:
            registered_common = Path(git(Path(project["path"]), "rev-parse",
                                         "--path-format=absolute", "--git-common-dir")).resolve()
        except RuntimeError:
            unresolved_paths.append(project["id"])
            continue
        if common == registered_common:
            matches.append(project)
    if len(matches) > 1:
        raise RuntimeError("multiple AO registrations match this Git repository")
    existing = matches[0] if matches else None
    if existing and existing.get("kind", "single_repo") != "single_repo":
        raise RuntimeError("workspace projects require their existing migration procedure")
    if existing and requested and requested != existing["id"]:
        raise RuntimeError("requested project ID differs from the existing registration")
    if not existing and unresolved_paths:
        raise RuntimeError("cannot prove repository is unregistered: unreadable AO project paths; repair stale registrations before adding another")
    if not existing and id_owner:
        raise RuntimeError("requested project ID belongs to another repository")
    if requested and not PROJECT_ID.fullmatch(requested):
        raise RuntimeError("invalid AO project ID")
    return root, primary, existing


def load_inspection(path, root):
    doc = json.loads(path.read_text())
    baseline.validate_inspection(doc, root)
    branch = doc["repository"].get("defaultBranch")
    if not isinstance(branch, str) or not branch:
        raise RuntimeError("inspection must resolve the default branch")
    git(root, "check-ref-format", "refs/heads/" + branch)
    return doc


def ensure_safe_targets(root, files):
    for relative in [*files, Path(".gitignore")]:
        target = root / relative
        for candidate in [target, *target.parents]:
            if candidate == root:
                break
            if candidate.is_symlink():
                raise RuntimeError(f"refusing symlinked baseline target: {relative}")
        if target.exists() and not target.is_file():
            raise RuntimeError(f"baseline target is not a regular file: {relative}")
    for relative, (text, _) in files.items():
        target = root / relative
        if target.exists() and target.read_text() != text:
            raise RuntimeError(f"existing project file differs: {relative}; reconcile explicitly before onboarding")


def require_clean(root):
    raw = run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    # AO injects this untracked runtime file before starting Qwen.
    entries = [x for x in raw.split("\0") if x]
    if any(x != "?? .qwen/settings.json" for x in entries):
        raise RuntimeError("repository must be clean except AO's untracked .qwen/settings.json")


def planned(doc, semantic):
    return baseline.planned_files(doc, semantic == "enabled")


def prepare(repo, requested, inspection, semantic):
    root, primary, existing = resolve(repo, requested)
    doc = load_inspection(inspection, root)
    files = planned(doc, semantic)
    ensure_safe_targets(root, files)
    require_clean(root)
    baseline.write_files(root, files)
    return {"repository": str(root), "registrationPath": str(primary),
            "projectId": existing["id"] if existing else requested,
            "registered": existing is not None,
            "files": [str(p) for p in files],
            "next": "Run scripts/verify, review and commit the baseline, make it available on the default branch, then run configure."}


def require_published_baseline(root, doc, files, preferred=None):
    branch = doc["repository"]["defaultBranch"]
    remotes = git(root, "remote").splitlines()
    preferred = preferred or os.environ.get("AO_REFRESH_REMOTE", "origin")
    if preferred in remotes:
        remote = preferred
    elif len(remotes) == 1:
        remote = remotes[0]
    else:
        raise RuntimeError("configure requires origin or one unambiguous Git remote for orchestrator refresh")
    ref = "refs/remotes/" + remote + "/" + branch
    remote_head = git(root, "symbolic-ref", "--quiet", "refs/remotes/" + remote + "/HEAD")
    if remote_head != ref:
        raise RuntimeError("recorded remote default branch differs from inspection; verify the remote and run git remote set-head before activation")
    git(root, "rev-parse", "--verify", ref + "^{commit}")
    for relative, (expected, _) in files.items():
        actual = run(["git", "-C", str(root), "show", f"{ref}:{relative.as_posix()}"])
        if actual != expected:
            raise RuntimeError(f"default branch baseline differs at {relative}; fetch/reconcile it before configuring AO")
    return ref


def merged_config(original, home, branch):
    config = copy.deepcopy(original)
    config["agentRules"] = f"Before any task action, read and follow the file at `{home / '.ao/rules/agentRules.md'}`."
    config["orchestratorRules"] = f"Before any coordination action, read and follow the file at `{home / '.ao/rules/orchestratorRules.md'}`."
    for role in ("worker", "orchestrator"):
        if role not in config:
            config[role] = {}
        if not isinstance(config[role], dict):
            raise RuntimeError(f"AO {role} config is malformed")
        config[role]["agent"] = "qwen"
    hooks = config.setdefault("postCreate", [])
    if not isinstance(hooks, list) or not all(isinstance(x, str) for x in hooks):
        raise RuntimeError("AO postCreate config is malformed")
    hook = f"python3 {shlex.quote(str(home / '.local/bin/ao-refresh-orchestrator'))} run"
    if hook not in hooks:
        hooks.append(hook)
    if config.get("defaultBranch") and config["defaultBranch"] != branch:
        raise RuntimeError("inspection default branch differs from the registered project's configuration")
    if not config.get("defaultBranch"):
        config["defaultBranch"] = branch
    return config


def save_config_backup(home, project):
    directory = home / ".local/state/ao-qwen-code-harness/project-onboarding"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, filename = tempfile.mkstemp(prefix="config-", suffix=".json", dir=directory)
    with os.fdopen(fd, "w") as stream:
        json.dump({"projectId": project["id"], "config": project["config"]}, stream, indent=2)
    return filename


def configure(repo, requested, inspection, semantic, home):
    root, primary, existing = resolve(repo, requested)
    doc = load_inspection(inspection, root)
    files = planned(doc, semantic)
    ensure_safe_targets(root, files)
    require_clean(root)
    preferred = ((existing or {}).get("config", {}).get("env") or {}).get("AO_REFRESH_REMOTE")
    ref = require_published_baseline(root, doc, files, preferred)
    run(["python3", str(ROOT / "install/verify_install.py"), "--home", str(home)])
    if not existing and not requested:
        raise RuntimeError("an unregistered repository requires --project-id")
    # Recheck identity immediately before registration/configuration.
    _, checked_primary, checked = resolve(root, requested)
    if checked_primary != primary or checked != existing:
        raise RuntimeError("AO registration changed during onboarding; inspect and retry")
    created = existing is None
    if created:
        run(["ao", "project", "add", "--path", str(primary), "--id", requested,
             "--name", doc["templateValues"]["PROJECT_NAME"],
             "--worker-agent", "qwen", "--orchestrator-agent", "qwen"])
        # Never delete a registration on uncertain CLI failure. A retry discovers it.
        existing = get_project(requested)
    project_id = existing["id"]
    config = merged_config(existing["config"], home, doc["repository"]["defaultBranch"])
    backup = None
    if config != existing["config"]:
        backup = save_config_backup(home, existing)
        if get_project(project_id) != existing:
            raise RuntimeError("AO project changed before configuration; no config write attempted")
        run(["ao", "project", "set-config", project_id, "--config-json",
             json.dumps(config, separators=(",", ":")), "--json"])
    after = get_project(project_id)
    if after["config"] != config or project_config_failures(after["config"], home):
        raise RuntimeError("AO config verification failed; inspect the saved config before retrying")
    return {"projectId": project_id, "registration": "created" if created else "reused",
            "baselineRef": ref, "configBackup": backup,
            "sessionNote": "Configuration applies to newly spawned sessions. Read the global rules in the current session; onboarding never restarts an orchestrator."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "prepare", "configure"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--project-id")
    parser.add_argument("--inspection", type=Path)
    parser.add_argument("--semantic-review", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            root, primary, existing = resolve(args.repo, args.project_id)
            result = {"repository": str(root), "registrationPath": str(primary),
                      "registered": existing is not None, "projectId": existing["id"] if existing else args.project_id,
                      "defaultBranch": existing["config"].get("defaultBranch") if existing else None,
                      "harnessRoot": str(ROOT),
                      "inspectionPrompt": str(ROOT / "templates/prompts/inspect-project.md"),
                      "inspectionSchema": str(ROOT / "config/project-inspection.schema.json"),
                      "templateManifest": str(ROOT / "templates/project/template-manifest.json")}
        elif not args.inspection:
            raise RuntimeError("--inspection is required for prepare/configure")
        elif args.action == "prepare":
            result = prepare(args.repo, args.project_id, args.inspection, args.semantic_review)
        else:
            result = configure(args.repo, args.project_id, args.inspection, args.semantic_review, args.home.resolve())
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"onboard-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

