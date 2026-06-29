# Tri-Agent Findings

## Validator A: Evaluator Behavior

Verdict: revise before implementation.

Findings:
- The default AutoResearch evaluator still resolved to `supervisor/autoresearch/evaluators/replay_corpus.py`, and generated experiments left evaluator refs blank, so they hit that default.
- The replay-corpus evaluator checks static corpus evidence and replay fixtures; it does not read `policy_candidate_changes`, `policy_overlay_candidate_ref`, `AUTORESEARCH_EMPTY_FLOOR`, or `AUTORESEARCH_CONTROL_KIND`.
- The mergeability evaluator is behavioral but was not the default and did not resolve policy candidate refs from the attempt worktree.
- The absence of the real powered benchmark does not block this slice; it only blocks benchmark promotion claims.

Folded changes:
- Default unresolved experiments now select `supervisor/autoresearch/evaluators/mergeability_bench.py` with `metric_name="mergeability_score"`.
- The mergeability evaluator now checks policy candidate refs and resolves relative candidates from `--attempt-worktree`.
- `tests/test_autoevolve_behavioral.py::test_behavioral_evaluator_reads_candidate` proves a good and bad candidate at the same worktree-relative policy candidate path produce different scores.

## Validator B: Benchmark Non-Routing

Verdict: revise before implementation.

Findings:
- The automatic report-derived path already rejects current blocked benchmark reports through applyability checks.
- A hidden explicit-candidate bridge existed: MCP `create_autoresearch_policy_proposals` can call `create_policy_evolution_proposals` with `candidate_changes`, bypassing the stricter report-derived metric-delta path.
- `promote_benchmark_report_to_autoresearch_report` only stamped `metric_applyable=false` and `improvement_claim_allowed=false` when blocked, leaving a future accepted benchmark-promotion report too close to an AutoResearch policy record.
- The promotion helper has no production callers in the repo, but it is a latent bridge.

Folded changes:
- Benchmark-promotion reports and records are now unconditionally `metric_applyable=false`, `improvement_claim_allowed=false`, `operator_facing_only=true`, and `policy_derivation_allowed=false`.
- Policy derivation rejects benchmark-origin records in both explicit and report-derived paths by record origin.
- `tests/test_autoevolve_behavioral.py::test_benchmark_report_not_on_derivation_path` covers both `derive_policy_evolution_proposals_from_report` and `create_policy_evolution_proposals`.

## Validator C: Human Gate

Verdict: revise before implementation.

Findings:
- There is no autonomous mutation path from AutoResearch or benchmark artifacts; proposal application is separate from proposal derivation.
- The literal `requires_operator_approval=true` invariant is not true after an approval event, because approved events correctly record `operator_approved=true`.
- The real gap was service-default identities: `codex-supervisor-axi` could satisfy the non-empty approver/operator check.
- AutoResearch reports did not explicitly carry `automatic_policy_mutation=false`, and the benchmark ledger authority flag set omitted `default_change_allowed=false`.

Folded changes:
- Policy approval/denial/rollback now reject reserved service identities such as `codex-supervisor-axi`.
- AXI no longer defaults approver/operator to `codex-supervisor-axi`.
- AutoResearch activation/parking also reject reserved service identities.
- AutoResearch summary reports now emit `automatic_policy_mutation=false`; benchmark ledger authority flags now include `default_change_allowed=false`.
- `tests/test_autoevolve_behavioral.py::test_adoption_requires_named_operator` proves a service-default approver cannot apply the proposal.
- `tests/test_codex_supervisor_axi.py::test_axi_experiments_activate_requires_named_operator` and `tests/test_codex_supervisor_axi.py::test_axi_policy_approve_requires_named_approver` prove the AXI CLI no longer supplies service defaults that bypass the human gate.

## Overall

Accepted after fold-back. The slice is implementable now as AutoResearch evaluator and human-gate hardening, while the real powered benchmark dependency remains honestly blocked and outside the adoption path.

## Post-Implementation Validation

Verdict: accepted after repair.

Findings:
- A read-only post-implementation validator found that replay-corpus rejection was path-sensitive: `evaluator_ref="supervisor/autoresearch/evaluators/replay_corpus.py"` was rejected, but an absolute path ending in that same evaluator could still derive a proposal.
- The behavioral evaluator path and human-gate path passed independent code validation.
- Operational readiness remains blocked until no-mistakes can run for this branch/head; the no-mistakes daemon reported an active run on another branch and would not start a new run for this branch.

Folded changes:
- Policy derivation now normalizes evaluator refs and rejects both canonical and absolute/path-equivalent replay-corpus evaluator refs.
- `tests/test_autoevolve_behavioral.py::test_replay_corpus_fixture_metric_rejected_as_adoption_signal` now covers the absolute-path replay-corpus bypass.
