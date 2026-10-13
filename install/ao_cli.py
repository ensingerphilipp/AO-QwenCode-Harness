#!/usr/bin/env python3
"""Keep the AO CLI bundled in the installed AppImage available through a stable path."""
from __future__ import annotations

import fcntl
import json
import os
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APPIMAGE = Path(".ao/agent-orchestrator.AppImage")
CACHE = Path(".local/state/ao-qwen-code-harness/ao-cli")
APPIMAGE_MEMBER = "resources/daemon/ao"


def find_appimage(home: Path) -> Path:
    """Find the operator-owned AppImage; never guess between multiple candidates."""
    canonical = home / APPIMAGE
    if canonical.is_file():
        return canonical
    candidates = set()
    for desktop in (
        home / ".local/share/applications/agent-orchestrator.desktop",
        home / ".local/share/applications/agent-orchestrator-ao-app.desktop",
    ):
        if not desktop.is_file():
            continue
        for line in desktop.read_text(errors="replace").splitlines():
            if not line.startswith("Exec="):
                continue
            try:
                cmd = shlex.split(line[5:])
            except ValueError:
                continue
            if cmd:
                path = Path(cmd[0])
                if path.is_absolute() and path.name.endswith(".AppImage") and path.is_file():
                    candidates.add(path.resolve())
    if len(candidates) == 1:
        return next(iter(candidates))
    if not candidates:
        raise RuntimeError(f"AO AppImage not found at {canonical} or registered desktop launchers")
    raise RuntimeError("Ambiguous AO AppImages in desktop launchers; resolve to one installation")


def ensure_shell_path(home: Path) -> bool:
    """Add a guarded PATH line in ~/.profile only when no existing line provides it."""
    profile = home / ".profile"
    if profile.exists() and (not profile.is_file() or profile.is_symlink()):
        raise RuntimeError("Refusing to modify non-regular/symlink ~/.profile")
    content = profile.read_text() if profile.exists() else ""
    if ".local/bin" in content:
        return False  # preserve the operator's existing PATH policy
    snippet = (
        "\n# AO-QwenCode-Harness: expose the user-scoped AO launcher\n"
        'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH";; esac\n'
    )
    stage = profile.with_name(".profile.ao-harness.tmp")
    if stage.exists() or stage.is_symlink():
        raise RuntimeError("PATH profile update has an existing temporary conflict")
    try:
        stage.write_text(content + snippet)
        stage.chmod(profile.stat().st_mode & 0o777 if profile.exists() else 0o644)
        os.replace(stage, profile)
    finally:
        stage.unlink(missing_ok=True)
    return True


def image_signature(image: Path) -> dict:
    st = image.stat()
    if not image.is_file() or not os.access(image, os.X_OK):
        raise RuntimeError(f"AO AppImage is not an executable regular file: {image}")
    return {"path": str(image.resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def ensure_ao_cli(home: Path) -> Path:
    home = home.expanduser().resolve()
    image = find_appimage(home)
    cache = home / CACHE
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = cache / "ao"
    meta = cache / "source.json"
    signature = image_signature(image)
    with (cache / ".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            known = json.loads(meta.read_text()) if meta.is_file() else None
        except (ValueError, OSError):
            known = None
        if known != signature or not binary.is_file() or not os.access(binary, os.X_OK):
            with tempfile.TemporaryDirectory(prefix=".ao-extract-", dir=cache) as tmp:
                result = subprocess.run(
                    [str(image), "--appimage-extract", APPIMAGE_MEMBER],
                    cwd=tmp, text=True, capture_output=True, timeout=120,
                )
                extracted = Path(tmp) / "squashfs-root" / APPIMAGE_MEMBER
                if result.returncode != 0 or not extracted.is_file():
                    raise RuntimeError("Cannot extract AO CLI from installed AppImage: " + result.stderr[:400])
                if image_signature(image) != signature:
                    raise RuntimeError("AO AppImage changed during CLI extraction; retry")
                stage = cache / "ao.new"
                try:
                    shutil.copy2(extracted, stage)
                    stage.chmod(0o700)
                    os.replace(stage, binary)
                    newmeta = cache / "source.json.new"
                    newmeta.write_text(json.dumps(signature) + "\n")
                    os.replace(newmeta, meta)
                finally:
                    stage.unlink(missing_ok=True)
        return binary


def main() -> int:
    try:
        binary = ensure_ao_cli(Path.home())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ao launcher: {exc}", file=sys.stderr)
        return 1
    os.execv(str(binary), [str(binary), *sys.argv[1:]])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
