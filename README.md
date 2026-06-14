# Codex Team Council

Codex Team Council packages the `/team` skill as a portable Codex plugin. It runs multiple model CLIs as independent floor workers, preserves their full outputs, creates recall capsules, sends the synthesis back for adversarial review, and returns one fortified answer with loud red flags for any missing model.

The plugin does not include provider credentials. Each user configures their own local roster and secrets.

## What Is Included

- `.codex-plugin/plugin.json`: Codex plugin manifest
- `skills/team/SKILL.md`: the `/team` skill instructions
- `skills/team/scripts/team_council.py`: the local council runner
- `skills/team/references/roster.example.json`: portable example model roster
- `assets/logo.png` and `assets/icon.png`: plugin artwork
- `scripts/setup_roster.py`: local roster bootstrap
- `scripts/package_zip.py`: clean zip packager
- `scripts/self_check.py`: package validation

## Install Locally

From this repo:

```bash
python3 scripts/setup_roster.py
python3 scripts/self_check.py
```

Install the plugin using the Codex plugin flow for a local plugin repo, or copy/symlink this repo into your personal plugin marketplace layout.

## Configure Models

The runner reads local config from:

```text
~/.codex/team/roster.json
```

Create it from the bundled example:

```bash
python3 scripts/setup_roster.py
```

Then edit `~/.codex/team/roster.json` for your machine:

- use provider CLI auth where possible
- set `binary` to commands on your `PATH` such as `opencode`, `claude`, or `codex`
- set `enabled: false` for models you do not want to spend on
- add new models by adding roster entries

## Secrets

Do not put literal API keys in this repo.

Preferred options:

- provider CLI login
- environment variables such as `KIMI_API_KEY` or `MOONSHOT_API_KEY`
- `api_key_file` pointing to a local file outside this repo, with `0600` permissions

The package ignores local rosters, env files, keys, run artifacts, and generated council output.

## Package A Zip

```bash
python3 scripts/package_zip.py
```

The zip is written to `dist/` and excludes `.git`, pycache, env files, local rosters, keys, and run artifacts.

## License

MIT
