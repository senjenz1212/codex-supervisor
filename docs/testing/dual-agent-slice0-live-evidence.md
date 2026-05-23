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

Not observed:

- No local `/lead` command file was found in user or plugin command paths.

Interpretation:

- Treat `/lead` as unproven/unavailable on this machine.
- The practical local worker-orchestration target is Claude Code agents plus
  the installed subagent-driven-development/feature-dev/code-review surfaces.

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
