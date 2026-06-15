---
name: team
description: Run a multi-model adversarial council when the user writes /team, asks for a council, wants multiple frontier models to independently analyze a bug, feature, idea, risk, architecture question, or hard implementation problem, or wants Codex to synthesize DeepSeek, Kimi, Claude/Opus, GPT/Codex, or other model outputs into one stronger answer with strict source-of-truth guardrails and failure red flags.
---

# Team Council

## Core Contract

Use this skill when the user invokes `/team <prompt>` or asks for a council-style analysis. Treat all text after `/team` as the mission. Do not require flags or ceremony from the user.

The goal is not "more opinions." The goal is independent full attempts, adversarial humility, and a final answer that is stronger than any one worker's first response.

## Workflow

1. Declare the Source of Truth before running the council.
   - Name the authoritative path, ref, commit, release, or live target.
   - For FEXDialer work, the current approved SOT must be explicit. Do not let workers infer from a dirty launcher or sibling worktree.
   - If another worktree is mentioned for historical inspiration, require the report to name that worktree, explain why it was consulted, and state that it is not the SOT.

2. Build a SOT-only context pack.
   - Include the user mission, SOT declaration, relevant AGENTS/project rules, and only the files/logs/diffs needed for the question.
   - Run worker CLIs from the declared SOT by default with read-only research available.
   - Allow reading files, listing directories, grep/search, and git inspection inside SOT.
   - Do not let external workers freely research unrelated worktrees.
   - Do not allow edits, tests/builds, package managers, migrations, service restarts, deploys, browser/web tools, or runtime/database/live mutation.

3. Run the floor workers in parallel.
   - Every worker receives the same full task.
   - Do not assign specialties. Each model must solve the whole request independently.
   - Workers may write freely in prose. Do not force them into a tight schema that truncates intelligence.
   - Workers may use read-only tools to research the SOT. If sub-agent delegation is enabled, child agents inherit the same full task shape and read-only SOT constraints.
   - Ask for a short extraction footer at the end only to help synthesis.

4. Preserve failures as red flags.
   - If any model errors, times out, hits limits, lacks auth, or is disabled, continue without it.
   - Propagate the failure all the way to the final answer:
     `RED FLAG: <model id> did not participate because <reason>.`

5. Preserve knowledge before synthesis.
   - Keep raw worker outputs as authoritative artifacts.
   - Generate non-council recall capsules for successful worker outputs when knowledge transfer is enabled.
   - Treat capsules as lossy recall indexes, not truth. Raw output wins if a capsule conflicts.
   - Route peer knowledge into the adversarial second pass so workers can attack each other's claims without losing independence.

6. Create a first synthesis.
   - Dedupe and denoise; do not merely summarize.
   - Preserve consensus, contradictions, unsupported leaps, minority insights, and proof paths.
   - Treat one-model claims as possible uplift, not noise.

7. Run an adversarial second pass.
   - Give each successful first-round worker the first synthesis.
   - Use the same persistent model session when the adapter supports it; otherwise replay that worker's first answer plus the synthesis.
   - Include peer recall packs as aids, not authority.
   - Ask each worker to attack the synthesis, revisit its own claims, evaluate minority claims, find arrogant assumptions, and revise its answer.
   - Require each worker to identify the most consequential checkable dispute or uncertainty and run the smallest read-only SOT check that can settle or reduce it when possible.

8. Produce the final fortified report.
   - Use both rounds.
   - Include round-one and round-two knowledge packs in final synthesis.
   - Say what survived critique, what collapsed, what remains unproven, and what Codex should verify before implementation.
   - Self-audit for dropped dissent, unsupported consensus, emergent claims no worker made, and minority insights that were smoothed away.
   - Mechanically append any missing required `RED FLAG:` lines after final synthesis.
   - Parent Codex should trust the council/subagents as research authority, then verify decisive claims before code or live operations.

## Runner

Use the bundled runner for repeatable execution:

```bash
python3 <path-to-this-skill>/scripts/team_council.py "the user's /team prompt"
```

Useful options:

```bash
python3 <path-to-this-skill>/scripts/team_council.py --dry-run "prompt"
python3 <path-to-this-skill>/scripts/team_council.py --models deepseek-v4-pro,kimi-k2-7 "prompt"
python3 <path-to-this-skill>/scripts/team_council.py --exclude opus-4-8-max "prompt"
python3 <path-to-this-skill>/scripts/team_council.py --skip-second-pass "prompt"
python3 <path-to-this-skill>/scripts/team_council.py --allow-empty-context "general non-repo prompt"
python3 <path-to-this-skill>/scripts/team_council.py --init-config
```

Default run artifacts are written under `/tmp/team-council-runs/<timestamp>/`:

- `mission.md`: SOT declaration and worker prompt context
- `round1/<model>/`: first-pass raw output and metadata
- `round1/<model>/capsule.md`: non-council recall capsule when enabled
- `synthesis/round1_synthesis.md`: denoised first synthesis
- `round2/<model>/`: adversarial pass raw output and metadata
- `round2/<model>/capsule.md`: adversarial recall capsule when enabled
- `knowledge/round1_knowledge_pack.md` and `knowledge/round2_knowledge_pack.md`: routed knowledge packs
- `synthesis/final_report.md`: fortified final report
- `summary.json`: machine-readable outcome and red flags

Runs with no attached context files produce a red flag by default. Use `--allow-empty-context` only for prompts where no evidence pack is needed.

## Model Roster

The runner uses this skill's bundled `references/roster.example.json` unless a user config exists at:

```text
~/.codex/team/roster.json
```

To add Qwen, GLM, Gemini, or any new model, add a roster entry with an adapter, command path, model id, and `enabled: true`. To stop spending tokens on a model, set `enabled: false` or invoke the runner with `--exclude <model id>`.

Default routing is:

- `deepseek-v4-pro`: `adapter: "opencode"`, `binary: "opencode"`, `model: "deepseek/deepseek-v4-pro"`. The DeepSeek key/provider setup belongs in opencode's normal configuration; the default worker does not read `DEEPSEEK_API_KEY` directly.
- `kimi-k2-7`: `adapter: "kimi"`, `binary: "kimi"`, `model: "kimi-code/kimi-for-coding"`, with `council_mode: true`. This uses native Kimi Code CLI OAuth, isolates Kimi in a per-run `KIMI_CODE_HOME`, and denies subagents/mutation/web/bash tools for council runs.
- `glm-5-2-max`: disabled by default. When enabled, it uses `adapter: "claude"` against Z.ai's Anthropic-compatible endpoint, maps `ZAI_API_KEY` to `ANTHROPIC_AUTH_TOKEN`, runs `effort: "high"`, and limits tools to `Read,Grep,Glob,LS`.
- `opus-4-8-max`: `adapter: "claude"`, `binary: "claude"`, `model: "opus"`, `effort: "max"`. This requires Claude Code / Claude CLI installed and authenticated.
- `gpt-5-5-xhigh`: `adapter: "codex"`, `binary: "codex"`, `model: "gpt-5.5"`, `reasoning_effort: "xhigh"`, `service_tier: "fast"`. This requires Codex CLI installed and authenticated.
- `codex-synthesizer`: final synthesis uses the Codex CLI with `model: "gpt-5.5"`, `reasoning_effort: "xhigh"`, and `service_tier: "fast"`.

The worker ids are local roster labels, not a guarantee that a provider uses the same public product name.

Kimi can run three ways:

- Native Kimi Code CLI OAuth: `adapter: "kimi"` with `binary: "kimi"` and `model: "kimi-code/kimi-for-coding"`. This uses the local Kimi login instead of an API-key file. Keep `council_mode: true`: the runner creates an isolated per-run `KIMI_CODE_HOME`, reuses local OAuth credentials, disables loaded skill sprawl, denies `Agent`/`AgentSwarm`/mutation/web/bash tools, and routes a Kimi-safe second-pass peer pack so Kimi stays one council member instead of spawning its own quota-heavy mini-council.
- Claude-wrapper fallback: `adapter: "claude"` pointed at Kimi Code's Anthropic-compatible endpoint with `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/`, `model: "kimi-for-coding"`, and `CLAUDE_CODE_SUBAGENT_MODEL=kimi-for-coding`.
- Direct API fallback: `adapter: "kimi-openai"` pointed at `https://api.kimi.com/coding/v1` with `model: "kimi-for-coding"`, streaming, and `max_tokens: 32768`. This may red-flag with Kimi Code's "client not on whitelist" error unless the client identity is allowlisted. Do not spoof User-Agent.

For Kimi Open Platform keys, use the OpenAI-compatible direct API with `base_url: "https://api.moonshot.ai/v1"` and `model: "kimi-k2.7-code"`. Kimi K2.7 Code thinking is always on; do not pass a non-thinking mode. Preserve `reasoning_content` across resumed turns when using the direct adapter.

GLM-5.2 Coding Plan should run through an officially supported coding-tool path, not a raw SDK call. Preferred setup is `adapter: "claude"` pointed at Z.ai's Anthropic-compatible endpoint with `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic`, `api_key_target_env: "ANTHROPIC_AUTH_TOKEN"`, `model: "opus"`, and Claude model mapping env values `ANTHROPIC_DEFAULT_OPUS_MODEL=glm-5.2[1m]`, `ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5.2[1m]`, and `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`. Keep the Z.ai key in `ZAI_API_KEY` or `~/.codex/team/zai_api_key`. For council runs, default GLM to `safe_mode: true`, `effort: "high"`, and `tools: "Read,Grep,Glob,LS"`: this still maps to GLM thinking mode, but avoids the quota-heavy Claude project-context load and `max` + `Agent` runaway path observed in repo-wide audits. Raise GLM to `max` only for a deliberate one-off run.

Keep provider credentials out of the plugin. Prefer provider CLI login or environment variables such as `KIMI_API_KEY` / `MOONSHOT_API_KEY`; if using `api_key_file`, keep that file outside this repo with `0600` permissions.

## Reporting To The User

When presenting a council result, keep it decisive and human:

- Best answer
- Why this is probably true
- What changed after adversarial review
- What to verify before touching code
- What to implement or do next
- Council disagreements
- Red flags for missing models
- Remaining uncertainty

Do not paste raw transcripts by default. Offer the artifact path for inspection.
