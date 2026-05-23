# Dual-Agent Delivery Slice 0: Reality Check PRD

## Status

Draft v0.1 - Slice 0 must pass before CS24 or any production dual-agent gate
implementation.

## Decision

Adopt **dual-agent gates, single-agent execution** as the target architecture:

- Codex and Claude Code both participate in every major decision gate.
- One agent owns each execution phase.
- The supervisor ledger is the source of truth for gate decisions, actions,
  artifact claims, and escalation state.
- Telegram is the escalation and approval surface.
- Cortex is the durable cockpit for artifacts, subagents, PRs, and evidence.
- Codex Desktop status sync remains `history_only`; live GUI repaint is retired
  as a dependency until a supported renderer-visible path is proven.
- Efficiency and delivery efficacy are the Slice 0 priority. Credential handling
  is a lightweight guardrail: prove the intended gateway and avoid obvious raw
  token leaks to operator-facing surfaces, but do not turn Slice 0 into an
  exhaustive secret-management project.
- Codex hands context to Claude Code through a typed handoff packet, not raw
  conversation history. The packet is written to `.handoff/<task_id>.json`,
  includes `packet_schema_version`, pins planning artifact checksums, pins the
  `/lead` skill version/hash, and names the outcome-validation failure policy.

Slice 0 is not a feature slice. It is the evidence gate that decides whether
the architecture is safe to build.

## Day 0 Prerequisite: Cockpit ADR

Before any probe starts, write a one-line ADR that chooses the cockpit target:

- Option A: wire the current Cortex cockpit now and accept a later
  strangler-fig/replacement path.
- Option B: build a minimal new cockpit for this supervisor flow.

This is a prerequisite, not a probe. P4, P5, P7, and all later milestone
rendering need a known destination for durable state and artifact links.

## Risk Types

| Surface | Risk type | Slice 0 treatment |
|---|---|---|
| Codex spawning Claude Code | Engineering/env | Prove once with P0/P1, then monitor drift. |
| Claude Code worker orchestration from spawned process | Engineering/env | Prove with P2 and high-volume capture. Use `/lead` if available; otherwise use the local Claude Code agent/superpowers equivalent. |
| Worker orchestration output to typed outcome | Engineering | Prove fidelity with P3 before Gate 5 exists. |
| Deadlock and budget exhaustion | Safety | Hard-stop probe P4. |
| Artifact exposure guardrail | Guardrail | Lightweight P5 before broad artifact publishing; does not outrank hard-stop probes. |
| Telegram delivery/control | Operational | Already mostly proven; add P7 rate limits. |
| ChatGPT mobile control | Product/env | Optional, prove with P9 before relying on it. |
| Computer-use validation | Operational | Optional validator only; prove with P8 if used. |
| Codex Desktop live GUI repaint | Structural | Retired. Treat as `history_only`, not a probe target. |

## Hard Stop Rule

Do not build CS24 or any production gate until these probes are green:

- P0 Credential Boundary
- P1 Spawn and Worktree Boundary
- P2 Worker Orchestration Invocation From Codex-Spawned Claude
- P3 Worker Output to `outcome.md` Adapter Fidelity
- P4 Deadlock and Budget Pause

If any hard-stop probe fails, revise the architecture first. Do not paper over
the failure with prompt text.

## Probe Dependency Graph

```text
Day 0 ADR
  |
  v
P0 -> P1 -> P2 -> P3 -> P6
        \          \
         \          -> P4 fixture can be refined with real schema later
          \
           -> P10 concurrent isolation

Parallel fixture/smoke probes after Day 0:
P4, P5, P7, P8, P9, P11
```

P4 and P5 begin as fixture stubs. They must not wait for production gate
plumbing. Their first purpose is to prove the state machine and artifact
policy can fail safely in isolation.

## Probe Matrix

### P0 Credential Boundary

Purpose: prove Codex-spawned Claude Code receives the intended auth and model
gateway, with an obvious-token check on operator-facing outputs.

Fixture:

- `tests/fixtures/dual_agent/p0_env_probe/expected_redactions.json`
- a fake Claude subprocess or local probe script that reports sanitized
  endpoint metadata.

Pass criteria:

- Spawn wrapper passes required env keys to Claude Code.
- The first outbound model request is proven to target the intended gateway
  host, for example Unity LiteLLM instead of direct Anthropic when configured.
- Env precedence is deterministic: explicit supervisor spawn env wins over
  ambient shell env unless the config says otherwise.
- Operator-facing summaries, SQLite rows, and Telegram text contain no obvious
  raw auth tokens. Local probe artifacts may keep sanitized endpoint metadata
  needed to debug gateway precedence.

Fail means:

- Wrong gateway or nondeterministic env precedence blocks the architecture.
  Obvious-token leaks to operator-facing surfaces are guardrail repairs; fix them
  quickly, but keep the main effort on proving spawn correctness.

### P1 Spawn and Worktree Boundary

Purpose: prove Codex can spawn Claude Code in the intended worktree and that
Claude edits only the approved target.

Fixture:

- temporary clean worktree with one tiny file-edit task.
- off-limits sibling worktree containing a sentinel file.

Pass criteria:

- Claude process starts from the approved cwd.
- It writes `outcome.md` in the approved worktree.
- It does not touch the off-limits sentinel.
- Git status shows only expected files.
- The supervisor records the run/worktree/task key.

Fail means:

- The architecture cannot assume handoff execution yet.

### P2 Worker Orchestration Invocation From Codex-Spawned Claude

Purpose: prove the exact invocation path can run Claude Code with a worker
orchestration surface, not just that Claude can answer interactively.

Fixture:

- Codex-owned spawn command invokes Claude Code with `/lead` if present, or
  the local equivalent: Claude Code `--agents`/`--agent`, `/feature-dev`,
  `/code-review`, or the installed `superpowers:subagent-driven-development`
  skill.
- A load case asks the worker orchestration surface to produce more than 5,000
  tokens of harmless specialist summary output.

Pass criteria:

- The orchestration surface runs from the Codex-spawned process.
- The Codex-owned spawn prompt references the typed handoff packet as the
  bounded context source.
- The evidence names the surface used, for example `/lead`, `claude_agents`,
  `feature-dev`, or `superpowers:subagent-driven-development`.
- stdout/stderr capture is complete under the load case.
- Output includes specialist names, decisions, conflicts/objections, and final
  summary.
- The capture mechanism is chunk-safe and does not truncate mid-JSON,
  mid-markdown, or mid-line.
- CI includes replay fixtures at 2K, 10K, 50K, and 200K token-size load levels
  through the same `/lead` invoker boundary. The replay source can be refreshed
  from live `/lead` output when Claude Code or `/lead` changes.

Fail means:

- Use a different worker mode or fix capture before continuing.

Current local finding:

- A portable global `/lead` command file now exists at
  `/Users/sam.zhang/.claude/skills/lead/SKILL.md`.
- `/lead` resolves through non-bare `claude -p`; `--bare` remains unsuitable
  for slash-command resolution.
- The global `/lead` command has been smoke-tested from both
  `codex-supervisor` and `muse-editor` with the typed
  `<dual_agent_outcome>` block.
- Unity Hub keeps a richer project-local `/lead` override under ignored local
  `.claude/skills` operational config.
- `claude agents` is available and reports configured agent surfaces.
- Claude Code agents plus installed subagent-driven-development/
  feature-dev/code-review skills remain the fallback worker surface.

### P3 Worker Output to `outcome.md` Adapter Fidelity

Purpose: convert native worker orchestration output into the typed
`outcome.md` contract without losing review-critical signal.

Fixture:

- saved worker transcript with at least three specialists, two decisions, one
  objection, changed files, tests, test status, and confidence.

Pass criteria:

- Deterministic parser maps transcript to schema.
- Claude JSON output from `--output-format json` fails loudly if the known
  `result: string` field is missing or type-drifted.
- All specialist names survive.
- All specialist decisions survive.
- All flagged objections/conflicts survive.
- Changed files, test commands, pass/fail status, and confidence values are
  present or explicitly marked unknown.
- If the adapter exceeds roughly 50 lines of domain logic, stop and consider
  consuming the orchestration surface's native format instead.
- Before judging implementation outcome, Codex verifies that immutable planning
  artifacts named in the handoff packet still match their handoff checksums.
- Outcome validation failure behavior is pinned by packet policy:
  malformed/missing outcome retries once with a corrective packet by default;
  fidelity failure, subprocess failure, and timeout abort to the operator by
  default.

Fail means:

- Gate 5 cannot safely rely on typed `outcome.md` yet.

### P4 Deadlock and Budget Pause

Purpose: prove budget exhaustion pauses instead of auto-deciding.

Fixture:

- fake gate runner where Codex always returns `deny` at high confidence and
  Claude always returns `accept` at high confidence until budget exhaustion.

Pass criteria:

- Gate stops at the configured per-gate cap or task budget.
- No implementation starts.
- No default winner is chosen.
- Artifacts and all gate rounds are preserved.
- Telegram escalation type is `kill_or_pause` or a more specific configured
  type.
- Supervisor action status is `paused_for_human`, not `failed`, `accepted`, or
  `delivered`.
- The escalation creates a nonce-protected Telegram ask with `Pause`, `Kill`,
  and `Continue`; callbacks update the action to `paused`, `kill_requested`, or
  `continue_requested` without directly executing destructive work.

Fail means:

- Unsafe autonomy. Do not build CS24.

### CS24 Gate Runner Boundary

Purpose: wire the hard-stop evidence into the actual gate sequence without
requiring the operator to manually prompt each phase.

Fixture:

- one or more gate specs with approved worktree, handoff artifacts, expected
  specialists, decisions, and objections.
- fake Claude runners replay valid, malformed, and schema-drift outputs.

Pass criteria:

- Each gate writes `.handoff/<task_id>.json`.
- Each gate invokes the `/lead` boundary with the packet path.
- P1 planning artifact checksums, P2 capture evidence, and P3 outcome fidelity
  are all present before a gate is accepted.
- Malformed outcome blocks retry once when the packet policy allows.
- Subprocess failure, timeout, schema drift, fidelity loss, or planning checksum
  changes block the gate.
- Multi-gate execution stops at the first blocked gate.

Fail means:

- Keep using manual Codex orchestration; do not advertise CS24 as autonomous.

### P5 Artifact Exposure Guardrail

Purpose: prove obvious credentials are not broad-published through the
dual-agent artifact path by default. This is a lightweight guardrail, not the
center of Slice 0.

Fixture:

- synthetic `handover.md`, `outcome.md`, and `gate_log.jsonl` containing fake
  tokens, URLs with keys, env vars, and personal data.

Pass criteria:

- Handoff, outcome, and gate logs keep task-relevant content intact.
- Known credential-like substrings are redacted before Telegram, Cortex/cockpit,
  or SQLite summaries.
- Artifact reads into Codex/Claude replace obvious token patterns where
  practical, without dropping implementation evidence needed for review.
- Raw secret fixtures remain only under test fixture paths.

Fail means:

- Do not broad-publish dual-agent artifacts to Telegram or Cortex until the
  guardrail is patched. Do not invalidate spawn, worker orchestration,
  deadlock, or claim verification evidence.

### P6 Test Coverage Gate

Purpose: prevent false green when code changed but no relevant tests changed.

Fixture:

- code diff touches implementation files.
- no test/eval/fixture file changes.

Pass criteria:

- Gate 5 asks exactly one bounded follow-up:
  "No tests appear to cover this change. Is this intentional?"
- The follow-up is at most 200 tokens.
- It is a single round.
- It does not count against the task dialogue budget.
- It does not auto-deny; human or Codex can accept risk when justified.

Fail means:

- Gate 5 can pass on vibes.

### P7 Telegram Rate Limit and Batching

Purpose: keep Telegram useful during busy gate cycles.

Fixture:

- synthetic burst of gate events: proposals, critiques, revisions, blocker,
  approval, and final milestone.

Pass criteria:

- Routine gate progress batches into a compact summary.
- Alerts, approvals, and blockers bypass FYI suppression.
- No more than one routine FYI per configured cooldown window.
- Final milestone always sends.
- Suppressed FYIs remain visible to later supervisor turns as quiet context.

Fail means:

- Telegram will drown the operator or forget context.

### P8 Computer-Use Validation Smoke

Purpose: prove computer-use can validate one GUI surface when truly needed.

Fixture:

- a deterministic local GUI or browser target with one visible success state
  and one visible failure state.

Pass criteria:

- Computer-use correctly distinguishes success/failure.
- Result is recorded as optional validation evidence.
- Failure does not block non-GUI validation ladders.

Fail means:

- Demote computer-use further; do not rely on it for Gate 5.

### P9 ChatGPT Mobile Control Smoke

Purpose: decide whether ChatGPT mobile can be a useful secondary control
surface.

Manual checks:

- Phone sees the active Codex thread.
- Phone approves a queued action and the approval reaches the Mac.
- Phone sees a diff or comparable artifact evidence.

Pass criteria:

- All three manual checks pass in the same connected-host setup.

Fail means:

- Keep Telegram and Cortex primary. Treat ChatGPT mobile as optional.

### P10 Isolation Under Parallelism

Purpose: prove probes and later parallel agent runs do not collide.

Fixture:

- run two synthetic probe instances simultaneously with distinct task ids,
  worktrees, and chat/update correlation ids.

Pass criteria:

- SQLite rows remain attributable to the correct task.
- Telegram messages do not interleave without task prefixes.
- Codex session aliases do not cross-resolve.
- Worktree sentinels are untouched.

Fail means:

- Add task-scoped correlation and locking before parallel runs.

### P11 Claim Verification

Purpose: prove the ledger is authoritative over agent prose.

Fixture:

- Claude writes `outcome.md` claiming "deployed to staging" or "cron armed"
  with no corresponding action row, tool output, PR check, or deployment event.

Pass criteria:

- Supervisor flags the claim as unverified.
- Gate 5 cannot pass until the claim is removed, verified, or escalated.
- Telegram/Cortex wording says "claimed but unverified", not "done".

Fail means:

- The ledger is decorative, not truth. Do not proceed.

## Calendar and Critical Path

Target schedule:

| Day | Target |
|---|---|
| Day 0 | Cockpit ADR written. Probe fixture skeletons created. |
| Day 1 | P0 green. P4/P5/P7 fixture REDs written. |
| Day 2 | P1 green. P8/P9 manual results recorded. |
| Day 3 | P2 green, including high-volume capture. P10 RED/GREEN. |
| Day 4 | P3 green with content-fidelity fixture. P11 RED/GREEN. |
| Day 5 | P6 green. Checkpoint: if more than two probes are red, stop adding work and triage. |
| Day 6 | Rerun hard-stop probes together. Repair docs and coverage map. |
| Day 7 | Slice 0 review: proceed to CS24, revise architecture, or stop. |

If P2 slips by more than one day, P3 and P6 automatically slip. Do not fake
their evidence with direct Claude output that bypasses the Codex-spawn path.

## Rollback and Invalidation Rules

- Probes are independent unless the dependency graph shows an arrow.
- A failed probe invalidates only downstream probes.
- P0 failure invalidates P1, P2, P3, and P6.
- P1 failure invalidates P2, P3, P6, and any real-worktree P10 result.
- P2 failure invalidates P3 and P6.
- P3 failure invalidates P6 and CS24 Gate 5 design.
- P4 failure blocks all production gates.
- P5 failure blocks broad operator-facing artifact publication but does not
  invalidate spawn mechanics or local gate validation.

## Replayability Requirement

For every non-manual probe, the same fixture input must reproduce the same
gate decision, action state, exposure-guardrail result, or failure
classification.

Replay must not call live Telegram, live target agents, live model APIs, or
Codex Desktop by default. Live smoke probes P8 and P9 must store only manual
evidence summaries, not secrets or raw screenshots unless a later issue
explicitly adds secure artifact handling.

## Forbidden Outcomes

- CS24 starts before P0, P1, P2, P3, and P4 are green.
- A gate chooses a winner at budget exhaustion.
- Agent prose is treated as verified action state without a ledger/tool/event
  match.
- Worker orchestration output is adapted to `outcome.md` while dropping
  specialist names, decisions, objections, changed files, test status, or
  confidence.
- A successful model call is treated as proof that the intended gateway was
  used.
- Code changes pass Gate 5 with no relevant tests and no bounded follow-up.
- Telegram quiet mode suppresses blockers, approvals, or final milestones.
- Parallel probes share task ids, worktrees, or ambiguous Telegram messages.
- Codex Desktop history append is described as live GUI reflection.
- Raw credentials are broad-published to Telegram, Cortex/cockpit, or SQLite, or
  known token substrings from handoff/outcome/gate logs are copied into operator
  summaries without lightweight redaction.

## Exit Criteria

Slice 0 exits in one of three states:

1. **Proceed to CS24**: P0, P1, P2, P3, P4 are green; P5, P6, P7, P10, P11 are
   green or have narrow accepted follow-up issues; P8/P9 are classified as
   optional or available.
2. **Revise architecture**: any hard-stop probe fails, or P3 proves the
   typed-outcome adapter loses too much signal.
3. **Stop**: Day 5 checkpoint finds more than two red probes with no clear
   repair path.
