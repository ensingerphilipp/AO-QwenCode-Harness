#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "templates/project"
MANIFEST = json.loads((TEMPLATE_ROOT / "template-manifest.json").read_text())
TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def run(cmd: list[str], *, check=True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(shlex.quote(x) for x in cmd)}\n{(proc.stderr or proc.stdout).strip()}")
    return proc


def require_clean_repo(repo: Path) -> None:
    top = Path(run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    if top != repo:
        raise RuntimeError(f"--repo must be the Git repository root: {top}")
    status = run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"]).stdout
    if status:
        raise RuntimeError("repository must be clean before initialization")


def validate_inspection(doc: dict, repo: Path) -> None:
    top_keys = {"schemaVersion", "repository", "facts", "templateValues", "verification", "reviewRisk", "decisionsRequired"}
    if not isinstance(doc, dict) or set(doc) != top_keys:
        raise RuntimeError("inspection has missing or unknown top-level fields")
    if doc.get("schemaVersion") != 1:
        raise RuntimeError("inspection schemaVersion must be 1")
    repository = doc.get("repository")
    if not isinstance(repository, dict) or set(repository) != {"root", "remote", "defaultBranch"}:
        raise RuntimeError("inspection repository object is malformed")
    decisions = doc.get("decisionsRequired")
    if not isinstance(decisions, list) or decisions:
        raise RuntimeError("inspection still contains unresolved human decisions")
    values = doc.get("templateValues")
    required_tokens = {token for tokens in MANIFEST["renderedFiles"].values() for token in tokens}
    if not isinstance(values, dict) or set(values) != required_tokens:
        raise RuntimeError("inspection templateValues does not match the template manifest")
    missing = sorted(token for token in required_tokens if token != "CI_SETUP_STEPS" and (not isinstance(values.get(token), str) or not values[token].strip()))
    if missing:
        raise RuntimeError("inspection has unresolved template values: " + ", ".join(missing))
    if not isinstance(values.get("CI_SETUP_STEPS"), str):
        raise RuntimeError("CI_SETUP_STEPS must be a string (empty is allowed)")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", values["CI_RUNNER"]):
        raise RuntimeError("CI_RUNNER must be one GitHub Actions runner label")
    if not values["CI_TIMEOUT_MINUTES"].isdigit() or not 1 <= int(values["CI_TIMEOUT_MINUTES"]) <= 999:
        raise RuntimeError("CI_TIMEOUT_MINUTES must be a decimal string from 1 through 999")
    setup = values["CI_SETUP_STEPS"]
    if setup and any(line and not line.startswith("      ") for line in setup.splitlines()):
        raise RuntimeError("CI_SETUP_STEPS lines must be indented six spaces for direct workflow insertion")
    root = repository.get("root")
    if root and Path(root).expanduser().resolve() != repo:
        raise RuntimeError(f"inspection repository root does not match --repo: {root}")
    verification = doc.get("verification")
    if not isinstance(verification, dict) or set(verification) != {"selectedProfiles", "preflight", "checks"}:
        raise RuntimeError("inspection verification object is malformed")
    if not isinstance(verification.get("checks"), list) or not verification["checks"]:
        raise RuntimeError("inspection must contain at least one verification check")
    for group in ("preflight", "checks"):
        if not isinstance(verification.get(group), list):
            raise RuntimeError(f"inspection verification.{group} must be an array")
        for item in verification[group]:
            if not isinstance(item, dict) or set(item) != {"id", "command", "rationale", "evidence"}:
                raise RuntimeError(f"inspection verification.{group} contains a malformed command")
            if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "command", "rationale")):
                raise RuntimeError(f"inspection verification.{group} contains an empty command field")
            if not isinstance(item.get("evidence"), list) or not item["evidence"]:
                raise RuntimeError(f"inspection verification.{group} command lacks evidence")
    risk = doc.get("reviewRisk")
    if not isinstance(risk, dict) or set(risk) != {"highRiskPaths", "highRiskLabels", "evidence"}:
        raise RuntimeError("inspection reviewRisk object is malformed")
    for key in ("highRiskPaths", "highRiskLabels"):
        if not isinstance(risk[key], list) or any(not isinstance(v, str) or not v.strip() for v in risk[key]):
            raise RuntimeError(f"inspection reviewRisk.{key} is malformed")


def render_text(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token not in values or not isinstance(values[token], str):
            raise RuntimeError(f"missing render token: {token}")
        return values[token]
    rendered = TOKEN_RE.sub(replace, text)
    leftovers = TOKEN_RE.findall(rendered)
    if leftovers:
        raise RuntimeError("unresolved template tokens remain: " + ", ".join(sorted(set(leftovers))))
    return rendered


def verification_script(doc: dict) -> str:
    template = (ROOT / "templates/verify/generic/scripts/verify").read_text()
    def block(items: list[dict]) -> str:
        lines = []
        for item in items:
            ident = item.get("id", "check")
            command = item.get("command")
            if not isinstance(command, str) or not command.strip():
                raise RuntimeError(f"verification command {ident!r} is empty")
            lines.append(f"printf '\\n==> %s\\n' {shlex.quote(ident)}")
            lines.append(command.rstrip())
        return "\n\n".join(lines) if lines else ": # no preflight commands"
    script = template.replace("{{VERIFY_PREFLIGHT}}", block(doc["verification"].get("preflight", [])))
    script = script.replace("{{VERIFY_STEPS}}", block(doc["verification"]["checks"]))
    if TOKEN_RE.search(script):
        raise RuntimeError("verification script contains unresolved tokens")
    return script


def planned_files(doc: dict, semantic_enabled: bool) -> dict[Path, tuple[str, int]]:
    values = dict(doc["templateValues"])
    files: dict[Path, tuple[str, int]] = {}
    for rel, _tokens in MANIFEST["renderedFiles"].items():
        source = TEMPLATE_ROOT / rel
        files[Path(rel)] = (render_text(source.read_text(), values), 0o644)
    for rel in MANIFEST["copyFiles"]:
        source = TEMPLATE_ROOT / rel
        text = source.read_text()
        if rel == ".qwen/review-config.json":
            risk = doc.get("reviewRisk", {})
            text = json.dumps({
                "schemaVersion": 1,
                "highRiskPaths": risk.get("highRiskPaths", []),
                "highRiskLabels": risk.get("highRiskLabels", []),
            }, indent=2) + "\n"
        elif rel == ".agent-harness.json":
            text = json.dumps({"schemaVersion": 1, "semanticReview": {"enabled": semantic_enabled}}, indent=2) + "\n"
        files[Path(rel)] = (text, 0o644)
    files[Path("scripts/verify")] = (verification_script(doc), 0o755)
    return files


def write_files(repo: Path, files: dict[Path, tuple[str, int]]) -> None:
    collisions = []
    for rel, (text, _mode) in files.items():
        target = repo / rel
        if target.exists() and (not target.is_file() or target.read_text() != text):
            collisions.append(str(rel))
    if collisions:
        raise RuntimeError("refusing to replace existing project files: " + ", ".join(collisions))
    for rel, (text, mode) in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(mode)

    fragment = (TEMPLATE_ROOT / "gitignore.harness.fragment").read_text().splitlines()
    ignore = repo / ".gitignore"
    existing = ignore.read_text().splitlines() if ignore.exists() else []
    additions = [line for line in fragment if line and line not in existing]
    if additions:
        content = "\n".join(existing)
        if content and not content.endswith("\n"):
            content += "\n"
        if content:
            content += "\n"
        content += "# AO + Qwen harness generated/runtime artifacts\n" + "\n".join(additions) + "\n"
        ignore.write_text(content)


def project_exists(project_id: str) -> bool:
    proc = run(["ao", "project", "get", project_id, "--json"], check=False)
    if proc.returncode == 0:
        return True
    message = (proc.stderr or proc.stdout).strip()
    if "PROJECT_NOT_FOUND" in message:
        return False
    raise RuntimeError(f"unable to determine AO project state for {project_id!r}: {message or 'unknown AO error'}")


def register_ao(repo: Path, project_id: str, name: str, doc: dict, home: Path, tracker_assignee: str | None) -> None:
    add = ["ao", "project", "add", "--path", str(repo), "--id", project_id, "--name", name,
           "--worker-agent", "qwen", "--orchestrator-agent", "qwen"]
    run(add)
    try:
        cmd = [
            "ao", "project", "set-config", project_id,
            "--default-branch", doc["repository"]["defaultBranch"],
            "--worker-agent", "qwen",
            "--orchestrator-agent", "qwen",
            "--agent-rules", f"Before any task action, read and follow the file at `{home / '.ao/rules/agentRules.md'}`.",
            "--orchestrator-rules", f"Before any coordination action, read and follow the file at `{home / '.ao/rules/orchestratorRules.md'}`.",
            "--post-create", f"python3 {shlex.quote(str(home / '.local/bin/ao-refresh-orchestrator'))} run",
        ]
        if tracker_assignee:
            cmd += ["--tracker-intake", "--tracker-assignee", tracker_assignee]
        run(cmd)
    except Exception:
        run(["ao", "project", "rm", project_id], check=False)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a fully resolved project baseline and register a new AO project.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inspection", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--semantic-review", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--tracker-assignee")
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    try:
        repo = args.repo.expanduser().resolve()
        require_clean_repo(repo)
        doc = json.loads(args.inspection.read_text())
        validate_inspection(doc, repo)
        if not doc["repository"].get("defaultBranch"):
            raise RuntimeError("inspection must resolve repository.defaultBranch before AO registration")
        home = args.home.expanduser().resolve()
        if not args.render_only:
            if project_exists(args.project_id):
                raise RuntimeError(f"AO project already exists: {args.project_id}; use migration tooling for existing projects")
            host_verify = run(["python3", str(ROOT / "install/verify_install.py"), "--home", str(home)], check=False)
            if host_verify.returncode != 0:
                raise RuntimeError("host-global harness installation is not verified: " + (host_verify.stderr or host_verify.stdout).strip())
        files = planned_files(doc, args.semantic_review == "enabled")
        write_files(repo, files)
        if not args.render_only:
            register_ao(repo, args.project_id, args.name or doc["templateValues"]["PROJECT_NAME"], doc, home, args.tracker_assignee)
        print(f"Initialized project baseline in {repo}")
        if args.render_only:
            print("AO registration skipped (--render-only).")
        else:
            print(f"Registered AO project: {args.project_id}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"init-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
