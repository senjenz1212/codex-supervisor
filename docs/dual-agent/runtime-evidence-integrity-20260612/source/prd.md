# PRD: Runtime-Evidence Integrity

## Problem Statement

The supervisor closeout left four verification gaps in the execution/outcome floor. First, `outcome_review` could baseline after an execution commit and miss committed implementation files. Second, TDD-named tests were visible in planning artifacts but not structurally required in supervisor-executed tests. Third, era-split transport incident data existed in trend details but was not visible in `codex-supervisor-axi trends`. Fourth, public poll-failure and Postgres trend parity coverage were incomplete. These gaps make the gates easier to accept with incomplete runtime evidence.

## Solution

Add pre-execution baseline persistence, a TDD execution-coverage receipt, era trend fields, public-boundary poll-failure tests, Postgres trend parity coverage, documented format A/B alternation, and a bounded advisory-lesson instruction guard. The runtime floor remains supervisor-owned and additive: new failures turn P11 red, while metrics and lessons stay observational/advisory and never mutate gate authority.

## User Stories

- As an operator, I want committed execution work to remain visible during `outcome_review`, even after a process restart.
- As a reviewer, I want every test named by the TDD plan to appear in supervisor-executed pytest targets before "tests passed" can count.
- As an operator deciding whether to migrate from MCP to AXI prompts, I want `trends` to show MCP-era and AXI-era incident counts and rates.
- As a maintainer, I want AXI and MCP poll failures to be tested at public boundaries.
- As a production owner, I want Postgres trend details and incident aggregation to match SQLite semantics.
- As an operator, I want advisory failure lessons to guide reviewers without becoming standalone blockers when current evidence disproves the prior failure mode.

## PRD Promise Contracts

P1. Execution baselines remain anchored to pre-execution HEAD

- User-visible promise: `outcome_review` can prove execution changes committed after the execution gate started.
- Public boundary: `CodexSupervisorMcpAPI.run_dual_agent_workflow` and the runtime baseline helper it invokes.
- Allowed outcomes: execution round 1 writes `runtime-baseline-execution.json`; later execution rounds do not overwrite it; `outcome_review` records `persisted_execution_baseline` or a visible fallback reason.
- Forbidden outcomes: committed execution files disappear from runtime diff evidence; marker write failure is invisible.

P2. TDD-named tests must be supervisor-executed

- User-visible promise: execution/outcome gates cannot accept when a TDD artifact names tests the supervisor did not execute.
- Public boundary: `collect_runtime_evidence`.
- Allowed outcomes: missing resolved tests produce `tdd_tests_not_executed`; unresolved or ambiguous names produce `tdd_test_names_unresolved`; full coverage stays green.
- Forbidden outcomes: agent prose or partial test lists satisfy a TDD plan.

P3. Trend output exposes era-split incident rates

- User-visible promise: `codex-supervisor-axi trends` shows MCP versus AXI transport incident counts and rates.
- Public boundary: AXI `trends` in JSON and TOON-lite modes.
- Allowed outcomes: JSON and TOON-lite expose `transport_incident_by_era`; JSON includes counts and rates.
- Forbidden outcomes: era details are persisted but hidden from operator output.

P4. Poll failures are recorded at public boundaries

- User-visible promise: missing-job polls through AXI and MCP write `transport_incident_observed` with `incident_type=poll_failure`.
- Public boundary: AXI `poll` and MCP `poll_dual_agent_workflow_job`.
- Allowed outcomes: both surfaces record poll-failure incidents with their interface labels.
- Forbidden outcomes: tests only exercise `record_transport_incident` directly.

P5. Postgres lane preserves trend detail semantics

- User-visible promise: Postgres can store trend `details_json` and aggregate incident events like SQLite.
- Public boundary: Postgres state lane.
- Allowed outcomes: detail JSON roundtrips; `read_events_since` feeds `record_quality_trends_for_run` incident aggregation.
- Forbidden outcomes: SQLite-only behavior is assumed portable.

P6. Format A/B metrics are produced by live TOON-lite polling

- User-visible promise: default AXI poll output emits format metrics that can be compared to JSON samples.
- Public boundary: AXI `poll` without `--json`.
- Allowed outcomes: `supervisor_axi_format_metric` records `format=toon`, byte count, and turn count.
- Forbidden outcomes: only synthetic format fixtures exist.

P7. Advisory lessons cannot override current gate evidence

- User-visible promise: a prior FM-1.3 or other lesson can guide review, but cannot by itself block a fresh run whose current handoff, artifacts, or source state materially differ.
- Public boundary: `build_lesson_injection` and the lead instruction block it produces.
- Allowed outcomes: the lesson block explicitly says lessons are checklist-only and that step-repetition applies only when current evidence proves the same handoff, artifacts, and source state.
- Forbidden outcomes: the lead blocks a sound gate solely because a matching historical lesson exists.

## Implementation Decisions

- Keep all new P11 reasons additive.
- Compare TDD test names against supervisor-generated runtime receipts, not caller-supplied receipts.
- Pass explicit planning artifacts into runtime evidence collection so PRD-to-TDD packets are enforceable.
- Keep trend metrics read-only and observational.
- Keep dispatcher/poll behavior non-blocking.
- Keep lesson injection advisory; current gate evidence remains authoritative.

## Testing Decisions

Use public-boundary tests first: workflow baseline helper behavior, `collect_runtime_evidence`, AXI CLI output, MCP poll, and Postgres lane tests. Run full pytest with `/Users/sam.zhang/.local/bin` on PATH so `uv`-shelling tests execute.

## Out Of Scope

- Changing reviewer-panel authority.
- Moving dispatcher work into poll/submit/catch-up.
- Changing fan-out defaults, Cursor review defaults, or AutoResearch policy mutation.
- Validating the separate Eevee proposal.
