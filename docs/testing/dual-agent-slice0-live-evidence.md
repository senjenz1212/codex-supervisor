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
- Selects `opus` for all best-quality gates, including execution, and `haiku`
  only when explicitly asked for cheap probes.
- Passes `--effort max` to Claude Code for `/lead` invocations by default.
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

## 2026-05-23 High-Volume Replay Harness

Implementation:

- `supervisor.dual_agent_runner.write_replay_fixture_family` writes replayable
  Claude stdout fixtures from a captured `/lead` transcript seed.
- `supervisor.dual_agent_runner.make_replay_runner` replays those stdout files
  through the same `invoke_claude_lead` boundary used by live calls.
- The deterministic suite covers 2K, 10K, 50K, and 200K token-size replay
  levels and asserts byte-for-byte stdout capture, JSON parse, P2 capture
  evidence, and P3 outcome fidelity.

Interpretation:

- CI now has a free regression for pipe/buffer truncation and JSON parse drift.
- Periodic live refresh remains operational: when Claude Code or `/lead`
  changes, refresh the seed transcript from a real `/lead` run and rerun the
  same replay family.

## 2026-05-23 Codex CLI MCP Dual-Agent E2E

Single-gate live path:

- Initiator: `codex exec --json` with the `codex_supervisor` stdio MCP server.
- Worker: live Claude Code `/lead` through `start_dual_agent_gate`.
- Task id / run id: `cli-dual-agent-e2e-20260523-091026`.
- Quality: `best`.
- Result: `accepted`.
- Probes: `P1 planning_artifact_boundaries_ok`, `P2 worker_orchestration_invocation_ok`,
  `P3 outcome_fidelity_ok`.
- Transcript readback: `read_gate_transcript` returned `status="ok"` and a
  handoff packet path.

Multi-round live path:

- Initiator: `codex exec --json` with the `codex_supervisor` stdio MCP server.
- Task id / run id: `cli-dual-agent-g3-20260523-091159`.
- Calls: three `start_dual_agent_gate` calls and three `record_gate_round`
  calls.
- Gate statuses: `accepted`, `accepted`, `accepted`.
- Round records: `revise/revise`, `revise/revise`, `accept/accept`.
- Transcript readback: `read_gate_transcript` returned `status="ok"` with
  three ordered rounds and final accepted result.

Observed model-tier finding:

- `quality="cheap"` is acceptable for deterministic wrapper tests but was
  flaky in live Codex-to-Claude e2e: one run produced a null specialist
  decision and another omitted the outcome block after retry.
- `quality="best"` passed the live single-gate and three-round flows. Desktop
  probe instructions now use `quality="best"` for the live G-2 path.
- Codex session spawning defaults to `gpt-5.5` with
  `reasoning_effort="xhigh"`.

Desktop GUI note:

- Computer Use could list Codex Desktop but refused to control
  `com.openai.codex` for safety reasons, so the GUI renderer itself was not
  automation-tested from this session.
- The CLI e2e proves the same repo-provided stdio MCP server, gate runner,
  handoff packet, Claude `/lead` invocation, SQLite event ledger, and transcript
  readback path that Desktop is configured to consume.
