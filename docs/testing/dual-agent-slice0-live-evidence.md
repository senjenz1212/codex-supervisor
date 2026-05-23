# Dual-Agent Slice 0 Live Evidence

## 2026-05-23 Minimal Claude Code Spawn

Command shape:

```text
claude --bare --no-session-persistence --debug-file /private/tmp/codex-supervisor-live-probe/claude-bare-haiku.log -p "Respond with exactly: OK" --output-format json --model haiku --max-budget-usd 1 --permission-mode dontAsk --tools ""
```

Result:

- Exit code: `0`
- Output result: `OK`
- Model: `claude-haiku-4-5-20251001`
- Cost: `$0.000657`
- Working directory: `/Users/sam.zhang/Documents/codex-supervisor`
- Debug evidence shows Claude Code loaded `ANTHROPIC_BASE_URL` and
  `ANTHROPIC_AUTH_TOKEN` from settings env without publishing token values.
- Sanitized local `ANTHROPIC_BASE_URL` host at probe time:
  `uai-litellm.internal.unity.com`

Interpretation:

- Live Claude Code subprocess invocation is proven for a minimal prompt.
- The low-cost `--bare --model haiku --tools ""` shape avoids the expensive
  default Opus/context load seen in the earlier failed budget probe.
- This is not yet a complete P2 worker-orchestration proof because it did not
  invoke a worker surface or produce high-volume specialist output.
- The debug log does not print the outbound request host header; if Slice 0
  needs direct host-header evidence, add a wrapper or proxy capture rather than
  treating a successful model call as proof.

## 2026-05-23 Claude Worker Surface Inspection

Observed local surfaces:

- `claude agents` reports configured agents including:
  `feature-dev:code-architect`, `feature-dev:code-explorer`,
  `feature-dev:code-reviewer`, `superpowers:code-reviewer`, and built-ins.
- Claude Code supports `--agent <agent>` and `--agents <json>`.
- Installed superpowers include `subagent-driven-development`, which describes
  implementer, spec-reviewer, and code-quality-reviewer subagent loops.

Original finding, superseded later the same day:

- No local `/lead` command file was found in user or plugin command paths.

Interpretation:

- Claude Code agents are a valid fallback worker-orchestration surface.
- Later probes installed and verified a portable global `/lead`; see below.

## 2026-05-23 Minimal Custom-Agent Spawn

Command shape:

```text
claude --bare --no-session-persistence --debug-file /private/tmp/codex-supervisor-live-probe/claude-agent-haiku.log --agents '{"slice0_lead":{"description":"Coordinates specialist review for Slice 0 probes","prompt":"You are a concise lead agent. Return only the requested structured evidence."}}' --agent slice0_lead -p "Return exactly: WORKER_SURFACE_OK" --output-format json --model haiku --max-budget-usd 1 --permission-mode dontAsk --tools ""
```

Result:

- Exit code: `0`
- Output result: `WORKER_SURFACE_OK`
- Model: `claude-haiku-4-5-20251001`
- Cost: `$0.000421`

Interpretation:

- Claude Code's `--agents`/`--agent` path works as the local worker surface.
- This proves the practical substitute for `/lead` can be invoked from Codex.
- Remaining P2 live proof is the high-volume/chunk-safe specialist-output case.

## 2026-05-23 High-Volume Worker Probe Attempt

Two high-volume custom-agent attempts were made:

- First attempt asked for a high-volume worker transcript and was refused
  because it looked like fabricated internal/system process evidence.
- Second attempt explicitly labeled the output as a synthetic local test
  fixture and was still refused because the requested padding looked
  deceptive.

Interpretation:

- Do not use "ask Claude to emit 5K filler tokens" as the P2 live load source.
- P2's deterministic validator remains useful for chunk-safe capture behavior.
- The remaining live P2 proof should use either a real worker run that naturally
  emits enough output, or a local non-model fixture producer below the same
  capture wrapper. It should not rely on persuading the model to generate
  padding.

## 2026-05-23 Portable Global `/lead`

Current command locations:

- Global portable command:
  `/Users/sam.zhang/.claude/skills/lead/SKILL.md`
- Unity Hub project-local override:
  `/Users/sam.zhang/Documents/unity-hub/.claude/skills/lead/SKILL.md`
- Unity Hub `.claude/skills` is ignored by repo policy, so that override is
  local operational config rather than version-controlled source.

Observed behavior:

- Non-bare `claude -p "/lead ..."` resolves the global `/lead` command from
  `codex-supervisor`.
- Non-bare `claude -p "/lead ..."` resolves the global `/lead` command from
  `muse-editor`.
- `--bare` remains inappropriate for `/lead` because it does not resolve the
  slash command.
- Both live probes ended with a parseable `<dual_agent_outcome>` block and
  passed `supervisor.dual_agent.evaluate_outcome_fidelity`.

## 2026-05-23 `supervisor.dual_agent_lead` Wrapper Smoke

Wrapper behavior:

- Builds a non-bare `claude --no-session-persistence -p "/lead ..."` command.
- Selects `opus` for best-quality decision gates, `sonnet` for best-quality
  execution, and `haiku` when explicitly asked for cheap probes.
- Captures stdout/stderr through an injectable runner.
- Parses Claude `--output-format json` output and validates the resulting
  transcript through `evaluate_outcome_fidelity`.

Live smoke from `codex-supervisor`:

- Gate: `intent`
- Quality: `cheap`
- Probe result: `P3 green outcome_fidelity_ok`
- Cost: `$0.0302968`
- Stdout bytes: `3873`
- Stderr bytes: `0`

Live smoke from `muse-editor`:

- Gate: `intent`
- Quality: `cheap`
- Probe result: `P3 green outcome_fidelity_ok`
- Cost: `$0.06103875`
- Stdout bytes: `3182`
- Stderr bytes: `0`

Interpretation:

- The portable `/lead` path is now good enough for gate-level smoke tests and
  typed outcome handoff.
- The high-volume P2 load proof is still separate. It should use a real worker
  run or a non-model fixture producer, not synthetic model padding.
