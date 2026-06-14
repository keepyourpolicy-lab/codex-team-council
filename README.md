# Codex Team Council

Codex Team Council packages the `/team` skill as a portable Codex plugin for higher-intelligence analysis inside Codex.

It exists because hard software work often fails in the gaps between models: one model misses a file-level constraint, another makes an elegant but unproven assumption, another sees the real edge case but buries it in prose. Team Council turns that into a repeatable method instead of a vibes-based "ask a few models" workflow.

The core idea is simple: every council member solves the whole problem independently, then the system preserves, critiques, and recombines their work into one stronger answer.

## Council Layers

1. Source-of-truth declaration: lock the council onto the live repo, worktree, commit, or artifact that actually matters.
2. SOT-only context pack: give every worker the same mission, rules, and evidence, while preventing accidental research in the wrong checkout.
3. Parallel floor workers: send the full task to every enabled model. No role splitting. Each model is responsible for the whole answer.
4. Raw artifacts: preserve each full worker output as the authority for later inspection.
5. Recall capsules: use non-council archivist passes to preserve claims, caveats, proof paths, minority insights, contradictions, red flags, and implementation details without making workers write rigid schemas.
6. First synthesis: dedupe and denoise the reports while preserving disagreements and one-model insights.
7. Adversarial second pass: send the synthesis and peer knowledge back to the original workers, preferably in the same persistent session, and ask them to attack assumptions, verify minority claims, and revise.
8. Final synthesis: produce one fortified report with what survived critique, what collapsed, what remains uncertain, and what Codex should verify before implementation.
9. Failure propagation: if any model fails because of auth, funds, limits, CLI errors, or local issues, the process continues and carries a loud `RED FLAG:` to the final answer.

## Why It Exists

This is not a committee for more opinions. It is a methodology for reducing blind spots:

- independent full attempts instead of model-specific task slicing
- evidence and proof paths instead of confident prose
- raw artifacts as authority instead of lossy summaries
- recall capsules to move knowledge between stages without throwing away minority insights
- adversarial review to fight the usual LLM failure mode of assuming its own first answer is right
- final Codex verification before implementation or live operations

## Early Uplift Evidence

In an early real debugging test, a separate worker reviewed the council's output against work it had already done. The headline architecture recommendation was not new; it validated what the worker had already suspected. The uplift came from the council correcting a wrong hypothesis and improving the action order.

Specifically, the council identified that a transferred lead was failing because of the actual access rule, not just bad test data. That turned a vague suspicion into a concrete production bug and changed the next fix. It also reframed availability as a first-order product decision and promoted a previously minor “topbar footgun” into a competing ownership problem.

That is the kind of intelligence increase this project is trying to make routine: not magic, not consensus worship, but a system that catches wrong assumptions, preserves the one weird useful insight, and returns a better implementation path than any one first pass.

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

## Local Credential Setup

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
