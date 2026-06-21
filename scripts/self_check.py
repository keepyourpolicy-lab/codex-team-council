#!/usr/bin/env python3
"""Validate the portable Team Council package."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gho_[A-Za-z0-9_]+)")
LOCAL_HOME_MARKER = "/home/" + "tim"
LOCAL_KEY_FILE_MARKER = "x" + "cratch"


def fail(message: str) -> None:
    raise SystemExit(message)


def check_manifest() -> None:
    manifest_path = REPO_ROOT / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ["name", "version", "description", "skills", "interface"]:
        if field not in manifest:
            fail(f"manifest missing {field}")
    interface = manifest["interface"]
    for field in ["composerIcon", "logo"]:
        asset = REPO_ROOT / interface[field].removeprefix("./")
        if not asset.exists():
            fail(f"manifest asset missing: {asset}")


def check_no_local_leaks() -> None:
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        forbidden = [LOCAL_HOME_MARKER] if path.name == ".gitignore" else [LOCAL_HOME_MARKER, LOCAL_KEY_FILE_MARKER]
        for needle in forbidden:
            if needle in text:
                fail(f"local path leak in {path}: {needle}")
        if SECRET_RE.search(text):
            fail(f"secret-shaped token found in {path}")


def check_python() -> None:
    runner = REPO_ROOT / "skills" / "team" / "scripts" / "team_council.py"
    subprocess.run([sys.executable, "-m", "py_compile", str(runner)], check=True)
    json.loads((REPO_ROOT / "skills" / "team" / "references" / "roster.example.json").read_text(encoding="utf-8"))


def check_runner_runtime_defaults() -> None:
    runner = REPO_ROOT / "skills" / "team" / "scripts" / "team_council.py"
    spec = importlib.util.spec_from_file_location("team_council_self_check", runner)
    if spec is None or spec.loader is None:
        fail("could not load team_council.py for runtime checks")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    run_root = module.DEFAULT_RUN_ROOT
    if not run_root.is_absolute():
        fail(f"DEFAULT_RUN_ROOT must be absolute: {run_root}")
    if str(run_root).startswith("\\tmp"):
        fail(f"DEFAULT_RUN_ROOT uses a Unix-style Windows root: {run_root}")

    probe = "arrow \u2192 snowman \u2603"
    proc = module.run_cmd(
        [
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.read(); print(data.encode('unicode_escape').decode('ascii'))",
        ],
        cwd=Path(tempfile.gettempdir()),
        input_text=probe,
    )
    if proc.returncode != 0:
        fail(f"UTF-8 subprocess probe failed: {proc.stderr.strip()}")
    if "\\u2192" not in proc.stdout or "\\u2603" not in proc.stdout:
        fail(f"UTF-8 subprocess probe lost unicode characters: {proc.stdout!r}")


def main() -> int:
    check_manifest()
    check_no_local_leaks()
    check_python()
    check_runner_runtime_defaults()
    print("self check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
