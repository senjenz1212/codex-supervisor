# PRD: Agentic Lead Provenance Foundation

## Intent

Implement an optional, provenance-graded foundation for agentic `/lead` under Codex supervision, without enabling live fan-out by default.

North star: Claude Code gets more dynamic only when Codex can verify where the evidence came from.

## Runtime Evidence

The Phase 0 probe in `../phase0-provenance-spike.md` found that Claude Code native helper fan-out is locally available, but high-stakes provenance cannot rely on native helper output alone. The stream exposed a helper task id and an ephemeral `/private/tmp/.../tasks/...output` file, but not a stable supervisor-owned teammate transcript path. Therefore:

- low/medium stakes may accept `lead_captured` evidence from lead/native fan-out;
- high stakes must require supervisor-owned, replay-verified worker evidence;
- `/lead` stays the integrator, while Codex owns provenance.

## Promise Contracts

### ALP-001: Agentic Lead Stays Off By Default

- User-visible promise: Existing `lead_direct` workflows keep their current behavior unless an operator explicitly opts into `agentic_lead_policy`.
- Public boundary: `codex_supervisor_mcp`
- Allowed outcomes: default workflow requests record `agentic_lead_policy=off`; no dynamic subagent receipts are required; existing P13/P14 behavior is unchanged.
- Forbidden outcomes: fan-out becomes default; existing `lead_direct` tests need dynamic receipts; gate sequence changes.

### ALP-002: Required Agentic Policy Blocks Weak Provenance

- User-visible promise: `agentic_lead_policy=required` blocks before `/lead` can claim success when required subagent evidence is missing or weak.
- Public boundary: `codex_supervisor_mcp`
- Allowed outcomes: P13 blocks on too few subagents, missing required roles, missing transcript/output refs, failed hash replay, lead solo/self execution without exception, or evidence grade below the required threshold.
- Forbidden outcomes: `/lead` self-stamps `runtime_native`; missing refs pass because the outcome says they exist; critical reviewer objections are deferred to prose.

### ALP-003: Evidence Grade Is Supervisor-Derived

- User-visible promise: Codex derives `self_reported`, `lead_captured`, or `runtime_native` from capture path and replay verification, ignoring any grade declared by `/lead`.
- Public boundary: `dual_agent_slice0`
- Allowed outcomes: verified supervisor-owned refs can reach `runtime_native`; verified non-supervisor refs can reach `lead_captured`; missing/unverified refs remain `self_reported`.
- Forbidden outcomes: a receipt's declared `evidence_grade` controls acceptance; unverified refs satisfy high-stakes policy.

### ALP-004: Runtime Fields Extend Existing Dynamic Receipts

- User-visible promise: Existing `dynamic_subagent_result` and P13/P14 paths are extended with runtime fields instead of duplicated.
- Public boundary: `dual_agent_slice0`
- Allowed outcomes: synthesized receipts preserve `agent_runtime`, `agent_id`, `permission_mode`, `tool_pins`, transcript/output refs, hashes, and critical review.
- Forbidden outcomes: a parallel receipt format bypasses existing P13 hash replay or P14 synthesis.

### ALP-005: Reliability Controls Precede Live Fan-Out

- User-visible promise: Per-agent timeout, budget cap, durable logs, and orphan cleanup are present before any live agentic mode is allowed.
- Public boundary: `dual_agent_runner`
- Allowed outcomes: helper worker metadata carries timeout/budget/log refs; orphan cleanup can terminate or mark stale workers without enabling live fan-out by default.
- Forbidden outcomes: live fan-out ships first and circuit breakers are added later.

### ALP-006: Independent Review Remains Downstream

- User-visible promise: Cursor and the configured fourth reviewer remain independent review gates after `/lead` synthesis; lead-spawned reviewers do not count as cross-vendor review.
- Public boundary: `codex_supervisor_mcp`
- Allowed outcomes: lead-spawned or supervisor-spawned subagents are execution evidence only; Cursor/fourth-reviewer decisions are still separate downstream blockers.
- Forbidden outcomes: lead subagents satisfy independent review requirements.

### ALP-007: Eval Gate Before Default Adoption

- User-visible promise: Agentic mode cannot become default until a replay/comparison report shows it beats `lead_direct` on representative historical tasks.
- Public boundary: `replay_cli`
- Allowed outcomes: report compares `lead_direct`, `agentic_allowed`, and `agentic_required` on wall-clock, cost, retries, rejected gates, missed issues, and operator interventions.
- Forbidden outcomes: default mode changes without measured comparison.

## Non-Goals

- Do not enable live fan-out by default.
- Do not replace or weaken P1/P2/P3/P13/P14.
- Do not build MCP transport auto-reconnect in this slice.
- Do not duplicate dynamic workflow receipt formats.
