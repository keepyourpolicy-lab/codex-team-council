#!/usr/bin/env python3
"""Create a clean distributable zip for Codex Team Council."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / ".codex-plugin" / "plugin.json"
EXCLUDED_NAMES = {
    ".git",
    "__pycache__",
    "dist",
    "tmp",
    "runs",
    "artifacts",
    "team-council-runs",
    "round1",
    "round2",
    "synthesis",
    "knowledge",
    "capsules",
    "state",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".env",
    ".key",
    ".pem",
}
EXCLUDED_FILENAMES = {
    "roster.json",
    "roster.local.json",
    "x" + "cratch",
    "mission.md",
    "run_meta.json",
    "summary.json",
    "stdout.txt",
    "stderr.txt",
    "output.md",
    "last_message.md",
}


def should_include(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT)
    parts = set(rel.parts)
    if parts & EXCLUDED_NAMES:
        return False
    name = path.name.lower()
    if name in EXCLUDED_FILENAMES:
        return False
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return False
    if "secret" in name or "token" in name:
        return False
    return path.is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description="Package Codex Team Council as a clean zip.")
    parser.add_argument("--output", help="Output zip path. Defaults to dist/<name>-<version>.zip.")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    name = manifest["name"]
    version = manifest["version"]
    output = Path(args.output).expanduser() if args.output else REPO_ROOT / "dist" / f"{name}-{version}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(REPO_ROOT.rglob("*")):
            if should_include(path):
                zf.write(path, path.relative_to(REPO_ROOT))

    print(f"created={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
