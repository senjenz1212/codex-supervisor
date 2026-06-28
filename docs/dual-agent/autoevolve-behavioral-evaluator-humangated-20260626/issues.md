# Issues

## 1. Select the behavioral evaluator by default

Tracer bullet: unresolved AutoResearch experiments resolve to the mergeability behavioral evaluator.

PRD promise: P1.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_replay_corpus_fixture_metric_rejected_as_adoption_signal`.

Public boundary: `resolve_evaluator_defaults`.

Chosen seam: default evaluator path and metric name.

Allowed: `mergeability_bench.py` with `mergeability_score`.

Forbidden: defaulting unresolved experiments to replay-corpus for adoption.

## 2. Resolve behavioral candidates from the attempt worktree

Tracer bullet: the evaluator reads the candidate artifact from `--attempt-worktree`.

PRD promise: P1.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_behavioral_evaluator_reads_candidate`.

Public boundary: the mergeability evaluator executable.

Chosen seam: relative candidate-ref resolution.

Allowed: good and bad candidate artifacts at the same worktree-relative path produce different scores.

Forbidden: falling back to source fixtures while ignoring the attempt worktree.

## 3. Carry evaluator provenance in AutoResearch records

Tracer bullet: validation records expose the evaluator that produced the metric.

PRD promise: P1.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_replay_corpus_fixture_metric_rejected_as_adoption_signal`.

Public boundary: `validate_attempt(...).to_payload()`.

Chosen seam: report record fields.

Allowed: `evaluator_ref` and `evaluator_hash` are included for policy-derivation screening.

Forbidden: policy derivation guessing evaluator identity from unrelated fields.

## 4. Reject replay-corpus as an adoption signal

Tracer bullet: a replay-corpus record that otherwise looks applyable produces no proposal.

PRD promise: P1.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_replay_corpus_fixture_metric_rejected_as_adoption_signal`.

Public boundary: `derive_policy_evolution_proposals_from_report`.

Chosen seam: record applyability guard.

Allowed: explicit skip/no proposal.

Forbidden: replay-corpus fixture metrics deriving a policy overlay proposal.

## 5. Block benchmark-promotion records from derivation

Tracer bullet: a benchmark-promotion-shaped record cannot derive even with positive metrics.

PRD promise: P2.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_benchmark_report_not_on_derivation_path`.

Public boundary: `derive_policy_evolution_proposals_from_report`.

Chosen seam: record applyability guard.

Allowed: benchmark evidence remains operator-facing.

Forbidden: benchmark-promotion records deriving AutoResearch policy proposals.

## 6. Preserve report-only benchmark flags

Tracer bullet: the benchmark path stays report-only and blocked from authority.

PRD promise: P2.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_benchmark_report_not_on_derivation_path`.

Public boundary: policy derivation over benchmark-shaped reports.

Chosen seam: applyability guard plus existing benchmark flags.

Allowed: no proposals, no mutation.

Forbidden: `improvement_claim_allowed` or policy mutation used to promote benchmark output.

## 7. Preserve named-operator approval

Tracer bullet: application still requires a non-empty approver and approval channel.

PRD promise: P2.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_adoption_requires_named_operator`.

Public boundary: `approve_policy_proposal`.

Chosen seam: `_require_operator`.

Allowed: a draft proposal remains draft until named approval.

Forbidden: anonymous or autonomous approval.

## 8. Preserve authority invariants

Tracer bullet: proposal and approval payloads keep automatic mutation disabled.

PRD promise: P2.

First public-boundary RED: `tests/test_autoevolve_behavioral.py::test_adoption_requires_named_operator`.

Public boundary: policy proposal and approval payloads.

Chosen seam: `_authority_invariants`.

Allowed: `automatic_policy_mutation=false` and `default_change_allowed=false`.

Forbidden: any default or gate authority flip.

## 9. Keep tests below live-infra boundaries

Tracer bullet: unit tests run local fixtures only.

PRD promise: P1, P2.

First public-boundary RED: all tests in `tests/test_autoevolve_behavioral.py`.

Public boundary: local evaluator executable and policy derivation functions.

Chosen seam: fixture mergeability candidates and local temp policy overlay files.

Allowed: fake files and local fixture bench below the public boundary.

Forbidden: live model, Docker, Pro oracle, or scaled benchmark execution in unit tests.

## 10. Record current benchmark dependency honestly

Tracer bullet: packet states the real benchmark is blocked and not consumed.

PRD promise: P2.

First public-boundary RED: planning artifact review.

Public boundary: packet and ledger.

Chosen seam: dependency status section.

Allowed: blocked dependency caveat.

Forbidden: claiming the real benchmark exists.

## 11. Commit source and tests before no-mistakes

Tracer bullet: land source plus focused tests together before running the gate.

PRD promise: P1, P2.

First public-boundary RED: focused pytest nodeids listed in TDD.

Public boundary: git commit plus no-mistakes gate.

Chosen seam: committed branch state.

Allowed: clean worktree with packet docs, source, and tests.

Forbidden: scratch/noise commits or no-mistakes on dirty source.
