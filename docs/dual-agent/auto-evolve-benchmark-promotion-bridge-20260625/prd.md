# Auto-Evolve Benchmark Promotion Bridge PRD

## Problem Statement

Operators need the next implementation sequence for "auto evolve AND real benchmark" to be blocked on real benchmark evidence, not on schema-complete planning artifacts. The existing packet correctly identifies a benchmark-to-AutoResearch evidence conversion bridge, but it still lets later bridge, power, and sink work appear runnable before the missing root artifact exists.

The missing first product step is **AEB-0: Real Official All-Arms Artifact Gate**. Before bridge, sink, annotation, or policy-proposal work may claim readiness, the supervisor must produce a real `official_all_arms_diagnostic_report.json` from pinned SWE-bench Pro or equivalent held-out inputs, or a blocked artifact with exact unavailable reasons. SWE-bench Verified may be used only as plumbing smoke unless the packet explicitly labels it as non-frontier evidence.

## Solution

Revise the packet so AEB-0 is the root gate for every real-evidence claim:

1. Produce or attempt a real official all-arms diagnostic artifact first.
2. Keep official all-arms and powered benchmark reports diagnostic and report-only.
3. Treat a missing AEB-0 artifact as a blocker for bridge readiness, sink readiness, annotation readiness, and policy proposal readiness.
4. Allow downstream issues to develop fixture-shaped guardrail tests, but require them to mark P1 real-evidence status as blocked until AEB-0 exists.
5. Convert benchmark-backed evidence into AutoResearch `records[]` only after AEB-0 and later power/quality gates qualify the evidence.
6. Keep Langfuse/Opik as a later read-only observability sink, never in the benchmark trust path.
7. Defer the maintainer-merge annotation queue until after the sink boundary is proven.

SWE-bench test pass is a held-out test-pass proxy, not human maintainer mergeability.

## User Stories

1. As an evaluator operator, I want a real official all-arms artifact or exact blocked artifact before any downstream work claims real evidence, so that schema-complete plans cannot masquerade as benchmark proof.
2. As an evaluator operator, I want the dataset pinned to SWE-bench Pro or a held-out equivalent for serious claims, so that Verified smoke is not overread as frontier benchmark evidence.
3. As a benchmark owner, I want produced single-agent baseline receipts to be generated and hash-checked, so that gold patches or metadata defaults cannot replace baseline behavior.
4. As a benchmark owner, I want official oracle receipts and report-integrity checks, so that missing or malformed harness buckets block rather than silently pass.
5. As a reviewer-gate owner, I want reviewers to receive public execution-grounded candidate packets, so that independent review never depends on protected oracle answers.
6. As a reviewer-gate owner, I want reviewer provenance, generator disjointness, pairwise agreement, pairwise oracle-error overlap, and effective-vote evidence linked to the same official rows, so that diversity claims are measurement-only until oracle-grounded errors exist.
7. As a statistician, I want powering to use paired discordance, target MDE, alpha, power, and CI width, so that raw `n_good`/`n_bad` thresholds do not masquerade as a powered experiment.
8. As an AutoResearch owner, I want benchmark-backed evidence converted into `records[]` only through an evidence conversion bridge, so that AutoResearch validation remains the policy-evolution gate.
9. As an operator, I want policy evolution to produce draft proposals only, so that default behavior changes require human approval.
10. As an observability operator, I want Langfuse/Opik ingestion to be a read-only sink after AEB-0, so that trace storage cannot affect benchmark trust decisions.

## PRD Promise Contracts

P0. A real official all-arms artifact is the root gate for all real-evidence claims.
- Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
- Chosen seam/interface: official all-arms diagnostic report dictionary and artifact path.
- Allowed outcomes: a real `official_all_arms_diagnostic_report.json` with report-only flags false; or a blocked artifact with exact unavailable reasons.
- Forbidden outcomes: bridge, sink, annotation queue, or policy proposal readiness is claimed without AEB-0.

P1. Real benchmark evidence is produced from pinned SWE-bench Pro or equivalent held-out official inputs, not fixtures alone.
- Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner` and powered benchmark runner.
- Chosen seam/interface: official all-arms diagnostic report and powered factorial report dictionaries.
- Allowed outcomes: Pro/held-out reports may be `completed`, `unavailable`, `underpowered`, or `report_only`; Verified evidence is labeled smoke unless explicitly justified.
- Forbidden outcomes: fixture-only or Verified-smoke rows unlock real benchmark readiness.

P2. Official all-arms diagnostic remains honest and report-only.
- Public boundary: `swebench_mergeability_official_all_arms_diagnostic_runner`.
- Chosen seam/interface: official all-arms report dictionary.
- Allowed outcomes: all policy/improvement flags remain false even when all arms populate.
- Forbidden outcomes: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, or `human_mergeability_claim_allowed` flips true on the diagnostic report.

P3. Reviewer diversity/independence metrics are emitted or linked on the same official rows.
- Public boundary: official all-arms report and reviewer-roster diagnostic report.
- Chosen seam/interface: reviewer provenance, generator disjointness, pairwise reviewer agreement, pairwise oracle error overlap, and effective-vote evidence blocks.
- Allowed outcomes: diversity metrics appear with explicit caveats and row/candidate pool hashes.
- Forbidden outcomes: self-preference, saturated agreement, or same-family decisive votes are hidden.

P4. Effective-vote claims require oracle-grounded reviewer errors, not just reviewer disagreement.
- Public boundary: reviewer-roster diagnostic report.
- Chosen seam/interface: effective-vote estimate and roster-selection guard.
- Allowed outcomes: effective-vote status is `computed` only when oracle-grounded reviewer errors exist.
- Forbidden outcomes: zero reviewer errors or disagreement-only evidence is described as reviewer independence.

P5. Powering is defined using paired/discordant evidence, target MDE, alpha, power, and CI width, not raw `n_good`/`n_bad` alone.
- Public boundary: powered factorial benchmark report.
- Chosen seam/interface: powered evidence contract in the benchmark report and evidence-conversion bridge.
- Allowed outcomes: report states whether power assumptions are met, underpowered, or blocked.
- Forbidden outcomes: raw class counts alone unlock metric applyability or evidence conversion.

P6. Produced single-agent baseline receipts are generated and verified, not assumed or replaced with gold patches.
- Public boundary: official all-arms runner and powered factorial report.
- Chosen seam/interface: baseline decision receipt normalization.
- Allowed outcomes: missing, malformed, hash-mismatched, or untrusted baseline rows mark the baseline unavailable.
- Forbidden outcomes: metadata acceptance, boolean legacy rows, or gold patch labels substitute for a produced baseline.

P7. Reviewers get execution-grounded/public candidate evidence sufficient for independent review.
- Public boundary: SWE-bench reviewer packet/public dashboard.
- Chosen seam/interface: public execution evidence packet.
- Allowed outcomes: public packet includes patch-apply receipts, public probe receipts, public checkout refs, protected-path exclusion, and hidden-oracle exclusion.
- Forbidden outcomes: hidden `FAIL_TO_PASS`, `PASS_TO_PASS`, test patches, protected-path content, or answer keys reach reviewer/public surfaces.

P8. Benchmark evidence can be converted into AutoResearch `records[]` only through an explicit evidence-conversion bridge.
- Public boundary: benchmark-to-AutoResearch evidence conversion bridge.
- Chosen seam/interface: AutoResearch validation attempt/report payload.
- Allowed outcomes: accepted AutoResearch records include evaluator execution provenance/hash, metric before/after/delta, quality controls, empty-floor comparison, and policy overlay candidate ref.
- Forbidden outcomes: benchmark report fields are passed directly to policy derivation without AutoResearch validation.

P9. Auto-evolution produces draft policy proposals only, with operator approval required before mutation.
- Public boundary: policy-evolution derivation and approval interfaces.
- Chosen seam/interface: `derive_policy_evolution_proposals_from_report` and proposal approval.
- Allowed outcomes: draft proposal records carry authority invariants and require operator approval.
- Forbidden outcomes: policy/default/gate mutation during benchmark evidence conversion or derivation.

P10. Observability sinks are read-only and outside the trust path.
- Public boundary: later Langfuse/Opik sink adapter.
- Chosen seam/interface: artifact-ingest sink that reads existing report artifacts and writes traces/observations only.
- Allowed outcomes: sink may ingest AEB-0 artifacts after they exist.
- Forbidden outcomes: sink output affects `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, `gate_advanced`, effective-vote status, roster selection, or policy derivation.

## Allowed Outcomes

- AEB-0 writes a real-input official all-arms artifact with all authority flags false.
- AEB-0 writes a blocked artifact with exact unavailable reasons.
- Later issues develop guardrail tests while real-evidence promises remain blocked until AEB-0 exists.
- Powered benchmark writes report-only evidence and labels it underpowered or blocked when paired, reviewer, baseline, or AEB-0 preconditions fail.
- Evidence conversion bridge creates AutoResearch attempt/record candidates only when benchmark-backed evidence is powered, evaluator-backed, hashable, quality-controlled, and operator-reviewed.
- Policy evolution derives draft proposals only from accepted AutoResearch records and never mutates policy without approval.
- Langfuse/Opik sink work waits for an AEB-0 artifact and remains read-only.

## Forbidden Outcomes

- Benchmark report flags are flipped directly to bypass AutoResearch validation.
- Official all-arms or powered benchmark reports are described as human maintainer mergeability.
- Fixture-only, smoke-only, underpowered, blocked, or no-AEB-0 evidence is converted into accepted AutoResearch records.
- Reviewer disagreement is reported as effective-vote evidence without oracle-grounded reviewer errors.
- Raw `n_good`/`n_bad` counts are treated as powered evidence without MDE/alpha/power/CI requirements.
- Missing or malformed produced baseline rows are silently replaced with metadata/gold-patch decisions.
- Missing or malformed official oracle buckets silently pass report-integrity checks.
- Hidden oracle/protected material reaches reviewer packets, dashboards, transcripts, public receipts, or observability sinks.
- Langfuse/Opik traces, tags, scores, or dashboards enter the benchmark trust path.
- The annotation queue starts before the read-only sink boundary is proven.
- Policy overlay/default/gate state mutates before operator approval.

## Implementation Decisions

- Add AEB-0 as the first implementation decision and root dependency.
- Use the existing official all-arms diagnostic as the real-input artifact interface and leave its report-only authority unchanged.
- Pin serious benchmark claims to SWE-bench Pro or an equivalent held-out official input; label Verified as smoke-only unless explicitly justified.
- Reframe official oracle integrity: empty-but-present `FAIL_TO_PASS` or `PASS_TO_PASS` buckets may be pass-compatible, but missing or malformed buckets are unavailable/blocked.
- Keep reviewer diversity/effective-vote as measurement gates; they do not select rosters or authorize policy without oracle-grounded reviewer errors.
- Use the existing powered factorial benchmark as the benchmark evidence interface, but extend the powering contract so paired discordance, MDE, alpha, power, and CI-width evidence are explicit.
- Add a new benchmark-to-AutoResearch evidence conversion module as a deep module with one small interface: convert a qualified benchmark-backed candidate and candidate overlay into an AutoResearch attempt/report payload plus ledger events.
- Keep AutoResearch validation and policy evolution as the policy authority; the evidence conversion bridge prepares records but does not apply policy.
- Keep Langfuse/Opik as a future read-only sink. It may ingest artifacts after AEB-0 but must never affect scoring, readiness, or policy derivation.

## Testing Decisions

- First tests hit public boundaries or chosen seams, not helper-only functions.
- Use one RED, one minimal GREEN, then repeat.
- Fake live SWE-bench/Docker/reviewer infrastructure below public boundaries in unit/integration tests, while requiring real-input-shaped fixtures and receipts in the test payloads.
- Include forbidden outcomes directly in tests: no AEB-0 artifact, hidden-oracle leak, missing baseline receipt, missing/malformed oracle receipt, underpowered report, no evaluator hash, no empty-floor comparison, reviewer disagreement without oracle-grounded errors, sink influencing trust flags, and mutation before approval.

## Out Of Scope

- Running the full live benchmark in this planning turn.
- Implementing the evidence conversion bridge in this planning turn.
- Implementing Langfuse/Opik sink ingestion in this packet revision.
- Drafting or implementing the maintainer-merge annotation queue.
- Mutating `.supervisor/policy-overlay.yaml`.
- Claiming human maintainer mergeability from SWE-bench test pass.
- Closing subagents.

## Repo Evidence

- Official all-arms diagnostic is diagnostic/report-only, requires all arms, and records the SWE-bench oracle limitation: `supervisor/swe_bench_mergeability.py:2575`, `supervisor/swe_bench_mergeability.py:2802`, `supervisor/swe_bench_mergeability.py:2916`, `supervisor/swe_bench_mergeability.py:2944`.
- No committed real all-arms diagnostic artifact was found in this packet; current all-arms completion evidence is fixture/fake-oracle test coverage until a real `official_all_arms_diagnostic_report.json` is produced: `docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/index.md:21`, `docs/dual-agent/auto-evolve-benchmark-promotion-bridge-20260625/tri-agent-findings.md:33`.
- Official replay and all-arms CLI paths are gated behind explicit dataset/prediction/oracle inputs: `supervisor/swe_bench_mergeability_cli.py:109`, `supervisor/swe_bench_mergeability_cli.py:170`.
- Produced baseline receipt normalization fails closed on missing, mismatched, malformed, or legacy baseline rows: `supervisor/mergeability_bench.py:4730`, `supervisor/mergeability_bench.py:4770`, `supervisor/mergeability_bench.py:4791`, `supervisor/mergeability_bench.py:4799`, `supervisor/mergeability_bench.py:4808`, `supervisor/mergeability_bench.py:4858`.
- Official oracle adapter compatibility is real, but missing `FAIL_TO_PASS` or `PASS_TO_PASS` buckets currently fall through `_status_for` as `pass`; AEB-0/AEB-1 must distinguish empty-present buckets from missing/malformed buckets before live claims: `supervisor/swe_bench_official_oracle.py:86`, `supervisor/swe_bench_official_oracle.py:177`, `supervisor/swe_bench_official_oracle.py:283`.
- Powered factorial reports include paired discordance, leave-one-reviewer-out, sample sufficiency, guardrails, and non-mutating flags: `supervisor/mergeability_bench.py:1993`, `supervisor/mergeability_bench.py:2002`, `supervisor/mergeability_bench.py:2007`, `supervisor/mergeability_bench.py:2043`, `supervisor/mergeability_bench.py:2051`.
- Reviewer roster diagnostics emit pairwise agreement, oracle-error overlap, effective-vote estimate, provenance, and generator disjointness while keeping policy flags false: `supervisor/mergeability_bench.py:2208`, `supervisor/mergeability_bench.py:2210`, `supervisor/mergeability_bench.py:2211`, `supervisor/mergeability_bench.py:2215`, `supervisor/mergeability_bench.py:2241`, `supervisor/mergeability_bench.py:2249`.
- Effective-vote and roster selection are blocked without oracle-grounded reviewer errors and enriched evidence: `supervisor/mergeability_bench.py:5254`, `supervisor/mergeability_bench.py:5265`, `supervisor/mergeability_bench.py:5297`, `supervisor/mergeability_bench.py:5327`.
- AutoResearch validation requires evaluator execution provenance/hash, metric trials, quality controls, and keeps default/policy/gate mutation false: `supervisor/autoresearch/validation.py:33`, `supervisor/autoresearch/validation.py:127`, `supervisor/autoresearch/validation.py:134`, `supervisor/autoresearch/validation.py:138`, `supervisor/autoresearch/validation.py:180`.
- AutoResearch report emits `records[]`, report-only recommendation, and operator review requirement: `supervisor/autoresearch/report.py:30`, `supervisor/autoresearch/report.py:36`, `supervisor/autoresearch/report.py:42`, `supervisor/autoresearch/report.py:45`, `supervisor/autoresearch/report.py:72`.
- Policy derivation rejects report/record applyability errors, requires evaluator provenance, quality controls, positive delta, empty-floor win, and candidate overlay refs, then creates draft proposals with authority invariants: `supervisor/autoresearch/policy_evolution.py:76`, `supervisor/autoresearch/policy_evolution.py:93`, `supervisor/autoresearch/policy_evolution.py:109`, `supervisor/autoresearch/policy_evolution.py:522`, `supervisor/autoresearch/policy_evolution.py:547`, `supervisor/autoresearch/policy_evolution.py:665`, `supervisor/autoresearch/policy_evolution.py:694`, `supervisor/autoresearch/policy_evolution.py:729`, `supervisor/autoresearch/policy_evolution.py:827`.
