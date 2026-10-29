#!/usr/bin/env python3
"""Locate the installed harness source and run its maintained onboarding helper."""
import json
import os
from pathlib import Path
import sys


def main():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    try:
        manifest = json.loads((state / "ao-qwen-code-harness/install-manifest.json").read_text())
        root = Path(manifest["sourceRepository"])
        if not root.is_absolute():
            raise ValueError("sourceRepository must be absolute")
        helper = root / "install/onboard_project.py"
        if not helper.is_file():
            raise ValueError("the installed harness source checkout is unavailable; restore it or reinstall from the current checkout")
        os.execv(sys.executable, [sys.executable, str(helper), *sys.argv[1:]])
    except (OSError, ValueError, KeyError) as exc:
        print(f"ao-init-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

