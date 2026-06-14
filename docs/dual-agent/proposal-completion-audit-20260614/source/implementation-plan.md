# Implementation Plan: Proposal Completion Audit

## Files / Modules To Touch

- `docs/dual-agent/proposal-completion-audit-20260614/source/prd.md`
- `docs/dual-agent/proposal-completion-audit-20260614/source/grill-findings.md`
- `docs/dual-agent/proposal-completion-audit-20260614/source/issues.md`
- `docs/dual-agent/proposal-completion-audit-20260614/source/tdd.md`
- `docs/dual-agent/proposal-completion-audit-20260614/source/grill-findings-tdd.md`
- `docs/dual-agent/proposal-completion-audit-20260614/source/implementation-plan.md`
- `docs/dual-agent/proposal-completion-audit-20260614/outcome-review.md`

## Steps

1. Replace generated source planning stubs with the PRD-to-TDD bundle in this directory.
2. Run local planning validation for every route gate to prove the deterministic planning floor is satisfied before invoking the lead again.
3. Resume or resubmit through the durable supervisor workflow with the same task intent and report-only visual policy.
4. Let the lead perform read-only source and ledger inspection for proposal completion.
5. Export an outcome report that includes completion status, liveness status, read-only command evidence, mutation boundary proof, and supervisor gate status.

## Risks

- The artifact exporter writes gate transcripts to root files such as `prd.md` and `tdd.md`; source artifacts must remain under `source/` so the planning validator reads the real contract.
- A report-only audit can be mistaken for operator approval. The lead must avoid `experiments activate`, `approve`, `deny`, apply, rollback, and overlay writes.
- Trend rows can be decision-adjacent but not decision-grade if they lack the denominator needed for reliability comparisons.
- Reviewer unavailable policy is block, so Cursor SDK outages should be reported as gate blockers rather than bypassed.

## Traceability

- P1 is tested by `test_report_classifies_proposal_completion_from_code` and implemented by the read-only source and ledger audit.
- P2 is tested by `test_report_only_audit_does_not_mutate_policy_or_queue` and implemented by pre-run/post-run overlay and queue checks.
- P3 is tested by `test_liveness_metrics_are_not_overclaimed` and implemented by denominator-aware trend interpretation.
- P4 is tested by `test_planning_artifacts_pass_all_route_gates` and `test_terminal_gate_status_is_reported_truthfully`; the durable workflow poll and exported transcripts provide the terminal evidence.
