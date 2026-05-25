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

## 2026-05-24 Live No-Budget Claude `/lead` Sandbox Probe

Scope:

- Task id / run id: `live-lead-no-budget-probe-20260524-01`.
- Cwd: disposable temporary git repository, not the `codex-supervisor` worktree.
- Cursor: disabled.
- Codex subagents: disabled.
- Gate invocation: direct `start_dual_agent_gate` calls through
  `CodexSupervisorMcpAPI`.
- Cost cap policy: no practical cap for the probe; each live gate was invoked
  with `quality="best"` and `budget_usd=100.0`, which produced
  `claude --model opus --max-budget-usd 100`.

Gate sequence:

- `prd_review`: accepted.
- `issues_review`: accepted.
- `tdd_review`: accepted.
- `implementation_plan`: accepted.
- `execution`: accepted.
- `outcome_review`: accepted.

Probe results:

- Every gate returned `P1 planning_artifact_boundaries_ok`.
- Every gate returned `P2 worker_orchestration_invocation_ok`.
- Every gate returned `P3 outcome_fidelity_ok`.
- Every gate returned `P_planning planning_validation_ok`.

Implementation evidence:

- Claude Code created `tests/test_slugify_label.py`.
- Claude Code created `sandbox_slug.py`.
- Codex ran `python3 -m pytest -q` in the sandbox after execution:
  `1 passed in 0.01s`.
- `read_gate_transcript` returned `status="ok"`.
- The ledger had 12 `dual_agent_interaction_message` events and, after Codex
  recorded review decisions, 6 `dual_agent_gate_round` events.
- Exported artifacts were written under
  `docs/dual-agent/live-lead-no-budget-probe-20260524-01/` in the sandbox and
  copied into this repo for inspection.

Claim-verification evidence:

- With receipts, `verify_workflow_claims` returned `P11 green
  workflow_claims_verified`.
- Without receipts, the same live outcome returned `P11 red
  workflow_claim_verification_failed` with:
  `tests_passed_without_test_receipt` and
  `implemented_without_diff_receipt`.

Captured fixtures:

- Live stdout fixtures and redacted metadata are checked in under
  `tests/fixtures/dual_agent/live_lead_no_budget_probe_20260524_01/`.
- `tests/test_dual_agent_live_lead_fixture.py` replays all six captured stdout
  files through `invoke_claude_lead` and asserts typed outcome parsing remains
  green.

Known caveats:

- This was not a Codex Desktop GUI run; it used the same Python MCP API boundary
  directly to avoid Desktop automation friction.
- The sandbox `git diff --name-only` was empty because new files were untracked;
  receipt evidence used `git status --porcelain=v1` to enumerate changed paths.
- The first script collected 12 interactions but no explicit round rows; Codex
  then recorded one accepted `dual_agent_gate_round` per gate and refreshed the
  artifact export.

## 2026-05-24 Cursor SDK Live Probe Diagnostic

Command shape:

```text
uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/live-cursor-sdk-probe-20260524-01
```

Result:

- Status: `skipped`
- Reason: `missing_cursor_api_key`
- `cursor_sdk` import: `ok`
- `CURSOR_API_KEY` present in process environment: `false`
- Fixture: `docs/dual-agent/live-cursor-sdk-probe-20260524-01/summary.json`

Interpretation:

- The live Cursor SDK probe harness exists and records a durable diagnostic
  fixture even when credentials are absent.
- This diagnostic is superseded by the 2026-05-25 green live Cursor probe
  below.

## 2026-05-25 Cursor SDK Live Probe

Command shape:

```text
uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/live-cursor-sdk-probe-20260525-01 --timeout-s 300
```

Result:

- Status: `completed`
- Probe: `CURSOR green cursor_review_ok`
- Cursor status: `finished`
- Model: `composer-2.5`
- Duration: `8720ms`
- Accepted: `true`
- `CURSOR_API_KEY` present in process environment: `true` (boolean only; key
  value is not stored)
- Fixture:
  `docs/dual-agent/live-cursor-sdk-probe-20260525-01/summary.json`
- Replay fixture:
  `tests/fixtures/dual_agent/live_cursor_sdk_probe_20260525_01/`

Interpretation:

- Cursor SDK integration is live-proven for a single read-only reviewer gate.
- Cursor returned a valid `Cursor Reviewer` typed outcome, accepted the review,
  and reported no changed files.
- This proves the SDK boundary and redacted fixture capture. It is not a full
  product workflow by itself; full workflow behavior is covered by workflow
  tests and the live failure-mode probe below.

## 2026-05-25 Live Tri-Agent Failure-Mode Probe

Command shape:

```text
uv run python scripts/probe_live_failure_mode.py --output-dir docs/dual-agent/live-failure-mode-probe-20260525-01 --fixture-dir tests/fixtures/dual_agent/live_failure_mode_probe_20260525_01 --timeout-s 900 --budget-usd 100
```

Scope:

- Cwd: disposable temporary git repository.
- Claude Code: live `/lead` through `run_dual_agent_gate`.
- Cursor: live SDK read-only reviewer through `invoke_cursor_agent`.
- Codex/supervisor: deterministic P11 claim verification with no test or
  git-diff receipts supplied.
- Source artifacts:
  `docs/dual-agent/live-failure-mode-probe-20260525-01/source/`.

Result:

- Final status: `blocked`
- Probe status: `blocked_as_expected`
- Claude gate status: `accepted`
- Claude probes: `P1 planning_artifact_boundaries_ok`,
  `P2 worker_orchestration_invocation_ok`, `P3 outcome_fidelity_ok`,
  `P_planning planning_validation_ok`
- Cursor probe: `CURSOR green cursor_review_ok`
- Cursor accepted fixture fidelity while noting the implementation/test claims
  were unsubstantiated in the worktree.
- Supervisor claim verification: `P11 red workflow_claim_verification_failed`
  with `tests_passed_without_test_receipt` and
  `implemented_without_diff_receipt`.
- Failure taxonomy:
  `task_verification / missing_or_stale_receipt`
- Fixture:
  `tests/fixtures/dual_agent/live_failure_mode_probe_20260525_01/`

Interpretation:

- This is the missing live failure-mode proof for the narrow scope.
- It demonstrates the core harness rule: even when Claude returns an accepted
  typed outcome and Cursor accepts the fixture as a reviewer, the supervisor
  ledger blocks completion when claims lack receipts.
- `tests/test_dual_agent_live_lead_fixture.py` replays the captured Claude
  stdout, parses both Cursor transcripts, and asserts the P11 blocked taxonomy
  remains stable.
