#!/usr/bin/env python3
"""Create a local Team Council roster from the bundled example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "skills" / "team" / "references" / "roster.example.json"
DEFAULT_TARGET = Path.home() / ".codex" / "team" / "roster.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create ~/.codex/team/roster.json from the bundled example.")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="Roster path to create.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing roster.")
    parser.add_argument("--disable-all", action="store_true", help="Create the roster with all models disabled.")
    args = parser.parse_args()

    target = Path(args.target).expanduser()
    if target.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing roster: {target}")

    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if args.disable_all:
        for model in payload.get("models", []):
            if isinstance(model, dict):
                model["enabled"] = False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass

    print(f"created={target}")
    print("Edit this file for local binary paths and enabled models.")
    print("Keep API keys in provider CLI auth, env vars, or an api_key_file outside this repo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
