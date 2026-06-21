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
7. Adversarial second pass: send the synthesis and peer knowledge back to the original workers, preferably in the same persistent session, and ask them to attack assumptions, verify minority claims, run the smallest useful read-only SOT check for a consequential dispute, and revise.
8. Final synthesis: produce one fortified report with what survived critique, what collapsed, what remains uncertain, and what Codex should verify before implementation, after auditing for dropped dissent, unsupported consensus, and emergent claims no worker actually made.
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
- keep native Kimi entries in `council_mode: true` so Kimi runs as one read-only council member, with subagents and mutation/web/bash tools denied and a native-CLI-safe second-pass peer pack

## Default Model Routing

The bundled roster is intentionally explicit about how each model is reached. The plugin does not hide provider setup behind a generic "AI model" label.

Default workers:

- `deepseek-v4-pro`: runs through the `opencode` CLI with model `deepseek/deepseek-v4-pro`. Install and authenticate `opencode`, then configure your DeepSeek provider/key in `opencode`'s normal provider config. This plugin does not read `DEEPSEEK_API_KEY` directly for the default DeepSeek worker.
- `kimi-k2-7`: runs through the direct Moonshot/OpenAI-compatible API route with model `kimi-k2.7-code-highspeed`. Set `MOONSHOT_API_KEY` locally; this uses `https://api.moonshot.ai/v1` and preserves Kimi K2.7 reasoning content across resumed council turns.
- `glm-5-2-max`: disabled by default. When enabled, it runs through the `claude` CLI against Z.ai's Anthropic-compatible endpoint, maps `ZAI_API_KEY` to `ANTHROPIC_AUTH_TOKEN`, uses `effort: "high"`, and limits tools to `Read,Grep,Glob,LS`.
- `opus-4-8-max`: runs through the `claude` CLI with local worker id `opus-4-8-max`, model `opus`, and effort `max`. Install and log in to Claude Code / Claude CLI before enabling this worker.
- `gpt-5-5-xhigh`: runs through the `codex` CLI with model `gpt-5.5`, reasoning effort `xhigh`, and service tier `fast`. Install and log in to the Codex CLI before enabling this worker.

Default synthesis:

- `codex-synthesizer`: also runs through the `codex` CLI with model `gpt-5.5`, reasoning effort `xhigh`, and service tier `fast`.

The roster labels are local ids used by the council runner. Check `~/.codex/team/roster.json` after setup if you want to change model ids, CLIs, effort settings, or which workers are enabled.

## Local Credential Setup

Do not put literal API keys in this repo.

Preferred options:

- provider CLI login
- environment variables such as `KIMI_API_KEY` or `MOONSHOT_API_KEY`
- `api_key_file` pointing to a local file outside this repo, with `0600` permissions

Practical defaults:

- DeepSeek credentials belong in your `opencode` setup for the default `deepseek-v4-pro` worker.
- Kimi credentials should be supplied as `MOONSHOT_API_KEY` for the default high-speed K2.7 route, unless you edit the roster to use `KIMI_API_KEY` or an `api_key_file`.
- Claude credentials belong in your `claude` CLI login for the default Opus worker.
- OpenAI/Codex credentials belong in your `codex` CLI login for the default GPT/Codex worker and synthesizer.

The package ignores local rosters, env files, keys, run artifacts, and generated council output.

## Package A Zip

```bash
python3 scripts/package_zip.py
```

The zip is written to `dist/` and excludes `.git`, pycache, env files, local rosters, keys, and run artifacts.

## License

MIT
