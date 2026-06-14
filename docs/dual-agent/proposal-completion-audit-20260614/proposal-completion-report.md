# Proposal Completion Audit

task_id: `proposal-completion-audit-20260614`
run_id: `8d7acabb-3d30-4bcc-8296-b65b7c99c8b4`
gate evidence path: `lead_direct`

This is the stable report artifact for the proposal-completion audit. The
root `outcome-review.md` is owned by the supervisor gate transcript exporter;
this file is the worker-authored report used by the runtime-TDD contract.

## Executive Verdict

The auto-improvement loop is implemented as a report-only, operator-gated loop,
but it is not fully live-proven. The code has real automatic pieces: run-end
lesson recording, run-end quality trend rows, run-end AutoResearch draft
generation, daemon cadence for runnable AutoResearch rows, and daemon cadence
for weekly P11 audits. The live ledger still shows the right side of the loop is
mostly unexercised: `experiments list` reports 60 rows with 20 draft, 0
runnable, 2 completed, 2 failed, and 36 parked; `doctor` reports
`pending_proposal_count=0`; trend rows contain only the empty overlay hash
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

## P1 Proposal Completion Matrix

| Area | Status | Code-grounded evidence | Remaining gap |
|---|---|---|---|
| AutoResearch draft generation | live | Run-end cleanup calls `generate_autoresearch_experiment_drafts` after quality trend recording in `mcp_tools/codex_supervisor_stdio.py:3724`; the generator clusters failure-taxonomy, reviewer-disagreement, probe-cohort, and lesson signals in `supervisor/autoresearch/generator.py:421`; draft rows are idempotently written at `supervisor/autoresearch/generator.py:144` and emit `autoresearch_experiment_drafted` at `supervisor/autoresearch/generator.py:163`. | Drafting is live, but the queue is capped and currently has 20 open drafts. |
| Runnable activation | implemented | The operator transition from draft to runnable is explicit in `supervisor/autoresearch/generator.py:183`; the AXI command path exposes `experiments activate` in `mcp_tools/codex_supervisor_axi.py:356`; the runner only consumes status `runnable` rows in `supervisor/autoresearch/generator.py:280`. | No runnable rows are present now, so the daemon has nothing to run. |
| Policy proposal derivation | implemented | The safer derived path is `derive_policy_evolution_proposals_from_report` in `supervisor/autoresearch/policy_evolution.py:74`; it derives a candidate overlay ref from accepted report evidence and writes `autoresearch_policy_proposal_created` in `supervisor/autoresearch/policy_evolution.py:136`; approval is still separate in `supervisor/autoresearch/policy_evolution.py:162`. | Derivation is implemented, but live-proven proposal creation is absent in this snapshot: `pending_proposal_count=0` and trend rows list no `policy_proposal_ids`. |
| Policy overlay liveness | live-unproven | The overlay reader is real: `load_policy_overlay` is in `supervisor/policy_overlay.py:124`; the instruction builder snapshots it at gate start in `mcp_tools/codex_supervisor_stdio.py:3864`; it applies the overlay to the instruction in `mcp_tools/codex_supervisor_stdio.py:3899`; the returned handoff records hashes in `mcp_tools/codex_supervisor_stdio.py:3939`. | Current overlay is absent, so the only observed overlay hash is the empty hash prefix `e3b0c44`; no non-empty overlay has been live-proven through a next run. |
| Regression rollback drafting | implemented | Regression comparison and rollback drafting exist in `supervisor/policy_overlay.py:234`; batch drafting from trend rows is wired by `draft_policy_regression_rollbacks_for_trend_rows` in `supervisor/policy_overlay.py:349`; run-end invokes it after trend rows in `mcp_tools/codex_supervisor_stdio.py:3691`. | It needs an applied proposal and enough after-window runs; current rows have no proposal id, so there is no live rollback case yet. |
| Lessons hygiene | live | `record_lessons_for_run` persists lessons from failure taxonomy, reviewer disagreement, drift, and sequence failures in `supervisor/lessons.py:235`; injection is built from matching lessons in `mcp_tools/codex_supervisor_stdio.py:3893`; feedback/retirement counters are recorded at run end in `mcp_tools/codex_supervisor_stdio.py:3817`. | The mechanism is live, but efficacy must be interpreted observationally; lessons remain advisory and cannot satisfy or block gates. |
| Weekly P11 audit | live | `WeeklyP11AuditTask` is defined in `supervisor/autoresearch/daemon_tasks.py:69`; it calls `run_weekly_p11_audit_if_due` and records health in `supervisor/autoresearch/daemon_tasks.py:92`; the daemon starts it in `daemon.py:206`; `quality_trends.py:421` writes schedule events and runs sampled P11 audits. | Current post-floor false-accept rate is still high enough to demand investigation, but it is a metric, not a gate verdict. |
| Runtime evidence floor | live | Runtime evidence is collected in `supervisor/runtime_evidence.py:58`; TDD coverage compares declared test names to executed or skipped-with-reason nodeids in `supervisor/runtime_evidence.py:489`; missing nodeids become `tdd_tests_not_executed` in `supervisor/runtime_evidence.py:514`. | The floor already caught this audit's prior artifact-clobber mistake; this report moves the stable deliverable off the exporter-owned path. |
| AXI detached-dispatcher | live | AXI poll is a thin read path over `poll_dual_agent_workflow_job` in `mcp_tools/codex_supervisor_axi.py:229`; submit delegates to the same API in `mcp_tools/codex_supervisor_axi.py:189`; home/status/experiments/trends/doctor are in `mcp_tools/codex_supervisor_axi.py:173`, `mcp_tools/codex_supervisor_axi.py:346`, `mcp_tools/codex_supervisor_axi.py:411`, and `mcp_tools/codex_supervisor_axi.py:419`. | Transport decision D1 remains undecidable until the trend data has enough MCP and AXI run denominators. |

## Report-Only Safety Invariant

The audit did not approve policy, activate experiments, deny experiments, apply
rollbacks, or mutate the policy overlay. The relevant live reads were
`doctor`, `experiments list`, and `trends`; all are observational.

| Surface | Pre/Post Observation | Mutated? |
|---|---|---|
| `.supervisor/policy-overlay.yaml` / `policy-overlay.yaml` | File absent; effective overlay hash is `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. | **No** |
| `experiments list` queue | 60 rows: 20 draft, 0 runnable, 2 completed, 2 failed, 36 parked. No activation was issued by this audit. | **No** |
| Policy proposals | `doctor` reports `pending_proposal_count=0`; trend rows have empty `policy_proposal_ids`. | **No** |
| Gate authority | All measured paths report `gate_authority=unchanged` or observational-only semantics. | **No** |

## D1/D2 Metric Decisions

D1 transport decision: undecidable. The trend row now exposes a denominator:
`transport_run_count_by_era={'axi': 46, 'mcp': 1}` with
`transport_decision_status=insufficient_data` and reason `need at least 5 mcp
and 5 axi runs`. The old incident-share interpretation is not decision-grade.

D2 format decision: undecidable. The trend row reports
`format_json_turns=180`, `format_toon_turns=0`,
`format_decision_status=insufficient_data`, and reason `need at least 5 toon
and 5 json samples`. There is not enough TOON traffic to set a default.

The runtime evidence floor is live; it is the only piece I would treat as
already proven by this audit because it blocked a stale artifact and forced this
stable report target.

## Gate Status And Runtime TDD

The audit was driven through the supervisor rigorous flow, not by manual
acceptance. The final gate sequence is accepted: `prd_review` attempt 1,
`issues_review` attempt 1, `tdd_review` attempt 1, `implementation_plan`
attempt 1, `execution` attempt 2, and `outcome_review` attempt 3. The
`outcome_review` repair exists because runtime evidence found the clobbered
root artifact and forced a stable report target. The relevant runtime TDD
contract is:

- `tests/test_proposal_completion_audit_20260614.py::test_planning_artifacts_pass_all_route_gates`
- `tests/test_proposal_completion_audit_20260614.py::test_report_classifies_proposal_completion_from_code`
- `tests/test_proposal_completion_audit_20260614.py::test_report_only_audit_does_not_mutate_policy_or_queue`
- `tests/test_proposal_completion_audit_20260614.py::test_liveness_metrics_are_not_overclaimed`
- `tests/test_proposal_completion_audit_20260614.py::test_terminal_gate_status_is_reported_truthfully`

## Critical Review

Strongest objection: the loop is implemented but not fully self-improving in
production until an operator activates at least one draft, the daemon completes
it, a policy proposal is derived, an operator approves or denies it, and trend
rows later prove or reject the overlay. The code contains the wires, but the
ledger currently says the right side is still mostly unproven.

What would change my mind: a future `experiments list` showing runnable rows
drained to completed, an `autoresearch_policy_proposal_created` event derived
from an accepted report, an operator approval event with overlay hash change, at
least three to five post-overlay runs, and a rollback/no-regression comparison
from `draft_policy_regression_rollbacks_for_trend_rows`.

## Remaining Pieces

1. Drain the 20 draft experiments deliberately: activate one or two high-signal
   drafts and park the rest with reasons.
2. Run one full AutoResearch cycle end to end: draft -> activate -> daemon run
   -> accepted report -> derived proposal -> operator decision.
3. Exercise a non-empty `.supervisor/policy-overlay.yaml` in one future run so
   overlay liveness moves from live-unproven to live-proven.
4. Wait for enough post-overlay trend rows before trusting regression rollback
   drafting as live-proven.
5. Do not decide D1 or D2 until trend rows show sufficient denominator coverage:
   at least 5 MCP and 5 AXI runs for D1, and at least 5 TOON and 5 JSON samples
   for D2.
