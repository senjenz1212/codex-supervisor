# Issues: Agentic Eval Bridge

## Slice 1: Arm Assembler

Priority: high

Scope: add `supervisor/agentic_eval_assembler.py` to convert one labeled-set
case into one three-arm dataset task by calling a supplied workflow runner for
each required mode.

Acceptance Criteria:

- [ ] The assembled task has exactly `lead_direct`, `agentic_allowed`, and
  `agentic_required` arms.
- [ ] Required verdicts from the labeled case are preserved.
- [ ] Each arm stores workflow_result, metrics, verdict_evidence, cassette ref,
  and budget split metadata from that arm.
- [ ] The assembled dataset loads through the existing `agentic_eval_runner`.

PRD promises: P1, P4.

## Slice 2: Equal Budget Split

Priority: high

Scope: implement a deterministic budget split helper that keeps the same total
budget across all modes while carving worker budgets from the fan-out total.

Acceptance Criteria:

- [ ] All arms expose the same `token_budget` and `budget_usd_limit`.
- [ ] Agentic arms include lead and worker budget shares whose sum is no larger
  than the task total.
- [ ] Unequal arm budgets are rejected before dataset export.
- [ ] Tests prove fan-out workers do not receive extra budget on top of lead.

PRD promises: P2, P5.

## Slice 3: Live Record/Replay CLI

Priority: high

Scope: add `scripts/run_agentic_eval_live.py` that selects a small subset,
refuses live calls by default, invokes the real workflow runner when opted in,
writes cassettes, assembles the dataset, and exports the comparison report.

Acceptance Criteria:

- [ ] Live execution exits nonzero unless `--allow-live-calls` is supplied.
- [ ] Per-arm workflow requests pass `agentic_lead_policy` as an argument.
- [ ] Recorded cassettes replay to a deterministic `report_sha256`.
- [ ] The CLI emits dataset and report artifacts without changing defaults.

PRD promises: P3, P4, P5.

## Coverage Index

- P1 covered by Slice 1.
- P2 covered by Slice 2.
- P3 covered by Slice 3.
- P4 covered by Slices 1 and 3.
- P5 covered by Slices 2 and 3.
