#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

FILE_MAP = {
    ROOT / "global/qwen/QWEN.md": Path(".qwen/QWEN.md"),
    ROOT / "global/ao/rules/agentRules.md": Path(".ao/rules/agentRules.md"),
    ROOT / "global/ao/rules/orchestratorRules.md": Path(".ao/rules/orchestratorRules.md"),
    ROOT / "global/ao/policies/semanticReviewPublication.md": Path(".ao/policies/semanticReviewPublication.md"),
    ROOT / "lifecycle/ao-refresh-orchestrator": Path(".local/bin/ao-refresh-orchestrator"),
}
SKILLS_SOURCE_ROOT = ROOT / "global/qwen/skills"
SKILLS_TARGET_ROOT = Path(".qwen/skills")
REQUIRED_COMMANDS = ("git", "gh", "ao", "qwen", "python3")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def command_version(command: str) -> str:
    proc = subprocess.run([command, "--version"], text=True, capture_output=True, timeout=15)
    text = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        raise RuntimeError(f"{command} --version failed: {text}")
    return text.splitlines()[0] if text else "unknown"


def check_commands(skip: bool) -> dict[str, str]:
    if skip:
        return {}
    versions = {}
    for command in REQUIRED_COMMANDS:
        path = shutil.which(command)
        if not path:
            raise RuntimeError(f"required command not found in PATH: {command}")
        versions[command] = command_version(command) if command in {"ao", "qwen"} else path
    ao_help = subprocess.run(["ao", "project", "set-config", "--help"], text=True, capture_output=True, timeout=15)
    required_flags = ("--config-json", "--agent-rules", "--orchestrator-rules", "--post-create", "--worker-agent", "--orchestrator-agent")
    if ao_help.returncode != 0 or any(flag not in ao_help.stdout for flag in required_flags):
        raise RuntimeError("installed AO CLI lacks the required project set-config surface")
    review_help = subprocess.run(["qwen", "review", "run", "--help"], text=True, capture_output=True, timeout=15)
    qwen_flags = ("--effort", "--json", "--fail-on", "--approval-mode", "--timeout-minutes")
    if review_help.returncode != 0 or any(flag not in review_help.stdout for flag in qwen_flags):
        raise RuntimeError("installed Qwen Code lacks the required native review-run surface")
    gh_help = subprocess.run(["gh", "pr", "checks", "--help"], text=True, capture_output=True, timeout=15)
    if gh_help.returncode != 0 or any(flag not in gh_help.stdout for flag in ("--required", "--json")):
        raise RuntimeError("installed GitHub CLI lacks required PR-checks support")
    return versions


def managed_sources() -> dict[Path, Path]:
    result = dict(FILE_MAP)
    for skill_dir in sorted(path for path in SKILLS_SOURCE_ROOT.iterdir() if path.is_dir()):
        for source in sorted(skill_dir.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix not in {".pyc", ".pyo"}:
                rel = source.relative_to(skill_dir)
                result[source] = SKILLS_TARGET_ROOT / skill_dir.name / rel
    return result


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("files"), dict):
        raise RuntimeError(f"invalid prior install manifest: {path}")
    return data


def install(args: argparse.Namespace) -> int:
    home = args.home.expanduser().resolve()
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")).expanduser().resolve()
    state_dir = xdg_state / "ao-qwen-code-harness"
    manifest_path = state_dir / "install-manifest.json"
    prior = load_manifest(manifest_path)
    versions = check_commands(args.skip_command_check)
    files = managed_sources()

    collisions = []
    operations = []
    for source, rel_target in files.items():
        target = home / rel_target
        new_hash = sha256(source)
        old_entry = prior["files"].get(str(rel_target))
        if target.exists():
            if not target.is_file() or target.is_symlink():
                collisions.append(f"non-regular target: {target}")
                continue
            current_hash = sha256(target)
            if current_hash == new_hash:
                operations.append(("noop", source, target, rel_target, new_hash))
                continue
            managed_unchanged = bool(old_entry and old_entry.get("sha256") == current_hash)
            if not managed_unchanged and not args.replace:
                collisions.append(f"unmanaged or locally modified target: {target}")
                continue
            operations.append(("replace", source, target, rel_target, new_hash))
        else:
            operations.append(("create", source, target, rel_target, new_hash))

    if collisions:
        raise RuntimeError("refusing installation:\n  - " + "\n  - ".join(collisions))

    if args.dry_run:
        for action, _, target, _, _ in operations:
            print(f"{action.upper():7} {target}")
        return 0

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = state_dir / "backups" / stamp
    installed = {}
    for action, source, target, rel_target, new_hash in operations:
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "replace" and target.exists():
            backup = backup_root / rel_target
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        if action != "noop":
            temp = target.with_name(target.name + ".ao-harness.tmp")
            shutil.copyfile(source, temp)
            os.chmod(temp, source.stat().st_mode & 0o777)
            os.replace(temp, target)
        installed[str(rel_target)] = {"sha256": new_hash, "mode": oct(source.stat().st_mode & 0o777)}

    # Remove stale files previously managed by this installer only when unchanged.
    for rel_text, entry in prior["files"].items():
        if rel_text in installed:
            continue
        target = home / rel_text
        if target.is_file() and not target.is_symlink() and sha256(target) == entry.get("sha256"):
            target.unlink()

    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {
        "schemaVersion": 1,
        "installedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sourceRepository": str(ROOT),
        "sourceCommit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, capture_output=True).stdout.strip(),
        "commandVersions": versions,
        "files": installed,
    }
    temp_manifest = manifest_path.with_suffix(".tmp")
    temp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp_manifest, 0o600)
    os.replace(temp_manifest, manifest_path)
    print(f"Installed AO + Qwen harness globals for {home}")
    print(f"Manifest: {manifest_path}")
    print("Qwen model/provider/auth settings were not modified.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install host-global AO + Qwen Code harness assets safely and idempotently.")
    parser.add_argument("--home", type=Path, default=Path.home(), help="Target user home (default: current user home)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--replace", action="store_true", help="Back up and replace conflicting unmanaged targets")
    parser.add_argument("--skip-command-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return install(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"install-host: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
