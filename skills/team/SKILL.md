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

8. Produce the final fortified report.
   - Use both rounds.
   - Include round-one and round-two knowledge packs in final synthesis.
   - Say what survived critique, what collapsed, what remains unproven, and what Codex should verify before implementation.
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
- `kimi-k2-7`: `adapter: "claude"`, `binary: "claude"`, `model: "kimi-k2.7-code-highspeed"`, with `ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic`. Provide `MOONSHOT_API_KEY`; the runner maps it to `ANTHROPIC_AUTH_TOKEN` only for the spawned Claude CLI process.
- `opus-4-8-max`: `adapter: "claude"`, `binary: "claude"`, `model: "opus"`, `effort: "max"`. This requires Claude Code / Claude CLI installed and authenticated.
- `gpt-5-5-xhigh`: `adapter: "codex"`, `binary: "codex"`, `model: "gpt-5.5"`, `reasoning_effort: "xhigh"`, `service_tier: "fast"`. This requires Codex CLI installed and authenticated.
- `codex-synthesizer`: final synthesis uses the Codex CLI with `model: "gpt-5.5"`, `reasoning_effort: "xhigh"`, and `service_tier: "fast"`.

The worker ids are local roster labels, not a guarantee that a provider uses the same public product name.

Kimi can run two ways:

- Preferred: `adapter: "claude"` pointed at Moonshot's Anthropic-compatible endpoint with `ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic`, `ANTHROPIC_AUTH_TOKEN` set from `MOONSHOT_API_KEY`, `model: "kimi-k2.7-code-highspeed"`, and `CLAUDE_CODE_SUBAGENT_MODEL=kimi-k2.7-code-highspeed`.
- Direct API fallback: `adapter: "kimi-openai"` pointed at `https://api.moonshot.ai/v1` with `model: "kimi-k2.7-code-highspeed"`, streaming, and `max_tokens: 32768`.

For Kimi Open Platform keys, use the OpenAI-compatible direct API with `base_url: "https://api.moonshot.ai/v1"` and `model: "kimi-k2.7-code-highspeed"`. Official docs describe the high-speed variant as the same Kimi K2.7 Code model with identical thinking behavior. Kimi K2.7 Code thinking is always on; do not pass a non-thinking mode. Preserve `reasoning_content` across resumed turns when using the direct adapter.

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
