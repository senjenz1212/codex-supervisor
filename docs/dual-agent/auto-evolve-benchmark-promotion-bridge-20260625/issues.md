# Auto-Evolve Benchmark Promotion Bridge Issue Pack

Each issue is a vertical tracer-bullet slice. First RED tests hit public boundaries or chosen seams; helper-only tests are not enough. AEB-0 is the root gate: no downstream issue can claim real benchmark readiness, sink readiness, annotation readiness, or policy proposal readiness until AEB-0 produces a real artifact or an explicit blocked artifact.

## AEB-0: Real Official All-Arms Artifact Gate

## PRD Promise

P0, P1, P2, P6, P7.

## What To Build

Produce or attempt a real `official_all_arms_diagnostic_report.json` from pinned SWE-bench Pro or equivalent held-out inputs. If the artifact cannot complete, write a blocked artifact with exact unavailable reasons. This gate is not a schema hardening exercise: it is the root evidence artifact that downstream bridge, sink, annotation, and policy proposal work must depend on for real-evidence claims.

## Public Boundary For First RED Test

`swebench_mergeability_official_all_arms_diagnostic_runner`.

## Chosen Seam/Interface

Official all-arms diagnostic report dictionary plus persisted `official_all_arms_diagnostic_report.json` artifact.

## Allowed Outcomes

- `status=completed` only when official replay is complete, all arms are available, produced baseline receipts verify, S_probe/S_full evidence is present, reviewer roster/public evidence is present, hidden leak check is clean, enough oracle good/bad rows exist, and matched TAR is computed.
- `status=unavailable` with exact `metrics_unavailable_reasons` when any prerequisite is missing.
- Verified data may be used only when labeled as plumbing smoke.
- All authority flags remain false in every outcome.

## Forbidden Outcomes

- A downstream bridge/sink/annotation/policy issue claims real-evidence readiness without AEB-0.
- A schema-only report is described as real benchmark evidence.
- Verified smoke is described as serious frontier benchmark evidence.
- Gold patch/metadata/legacy bool replaces produced baseline.
- Missing S_probe, S_full, reviewer, baseline, oracle, or leak-check evidence is imputed.
- Diagnostic report claims powered improvement, human maintainer mergeability, policy mutation, or gate advancement.

## First RED Test

Add a public-boundary test where no real all-arms artifact exists and downstream bridge readiness is requested. The test should expect `status=blocked`, reason `real_official_all_arms_artifact_required`, and false authority flags.

Add artifact-shape RED tests where a real-input-shaped all-arms artifact is missing a produced baseline receipt or official oracle receipt. Each must stay blocked/unavailable and preserve all false authority flags.

## Minimal GREEN Target

Add root-gate status/report fields and blocked-readiness checks at the official all-arms boundary. Do not change policy flags.

## Ledger Events/Artifacts

- `stage=real_official_all_arms_artifact_gate`
- `evidence_kind=official_oracle`
- artifact: `official_all_arms_diagnostic_report.json`
- blocked reasons: missing real artifact, missing baseline receipt, missing official oracle receipt, missing S_probe, missing S_full, missing reviewer public evidence, hidden leak, under-minimum good/bad, matched TAR unavailable, Verified smoke only.

## Acceptance Criteria

- Packet and tests name AEB-0 as the root gate.
- AEB-0 artifact or blocked artifact exists before downstream real-evidence claims.
- Dataset is pinned to SWE-bench Pro or held-out equivalent for serious claims; Verified is smoke-only.
- Report includes produced baseline status, oracle receipt status, arm availability, reviewer public evidence status, hidden leak status, oracle limitation note, and report-only flags.
- `metric_applyable=false`, `improvement_claim_allowed=false`, `powered_improvement_claim_allowed=false`, `human_mergeability_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## Blocked By

None - can start immediately.

## AEB-1: Official All-Arms Evidence Packet Hardening

## PRD Promise

P1, P2, P6, P7, P10.

## What To Build

After AEB-0 exists or is explicitly blocked, harden the official all-arms evidence packet so the report records official replay, produced baseline receipts, S_probe, S_full reviewer evidence, oracle ceiling, public reviewer packet evidence, exact blocked/report-only reasons, and report-integrity status in one operator-facing artifact.

## Public Boundary For First RED Test

`swebench_mergeability_official_all_arms_diagnostic_runner`.

## Chosen Seam/Interface

Official all-arms diagnostic report dictionary.

## Allowed Outcomes

- Report hardening may improve blocked-reason clarity without changing authority flags.
- Empty-but-present `FAIL_TO_PASS` or `PASS_TO_PASS` buckets remain pass-compatible with upstream semantics.
- Missing or malformed status buckets become unavailable/blocked report-integrity failures.
- Report-only flags remain false for every outcome.

## Forbidden Outcomes

- Empty-present buckets are treated as malformed.
- Missing/malformed buckets silently pass report integrity.
- Missing S_full reviewer evidence is imputed as accept.
- Hidden oracle or protected material reaches reviewer/public surfaces.
- Diagnostic report claims powered improvement or human maintainer mergeability.

## First RED Test

Add a public-boundary oracle-integrity test: an empty-but-present `FAIL_TO_PASS` or `PASS_TO_PASS` bucket may pass, but a missing or malformed bucket must make the oracle receipt unavailable/blocked and leave authority flags false.

## Minimal GREEN Target

Normalize report-integrity fields and blocked reasons at the all-arms boundary without changing policy flags.

## Ledger Events/Artifacts

- `stage=official_all_arms_evidence_packet_hardening`
- `evidence_kind=official_oracle`
- artifact: `official_all_arms_diagnostic_report.json`
- blocked reasons: malformed baseline, malformed oracle bucket, hidden leak, missing reviewer public evidence.

## Acceptance Criteria

- Official all-arms report includes all arm availability, baseline receipt verification, public evidence summary, hidden leak status, oracle limitation note, oracle report-integrity status, and report-only policy flags.
- Missing or malformed baseline rows are unavailable, not replaced.
- Missing/malformed official oracle status buckets are unavailable/blocked; empty-present buckets remain pass-compatible.
- SWE-bench test pass is labeled as held-out test-pass proxy, not maintainer mergeability.

## Blocked By

AEB-0 for real-evidence claims. Fixture-shaped hardening tests may run before AEB-0 but must mark P1 real-evidence status blocked.

## AEB-2: Powered Factorial Statistical Contract

## PRD Promise

P1, P2, P5, P6, P10.

## What To Build

Extend the powered factorial benchmark report to record an evidence-conversion statistical contract: paired discordance, target MDE, alpha, power, CI width, same candidate pool hash, baseline availability, full-stack availability, and underpowered/blocking reasons.

## Public Boundary For First RED Test

`run_powered_factorial_mergeability_evaluation`.

## Chosen Seam/Interface

Powered factorial report dictionary and its `promotion_guardrails`/power block.

## Allowed Outcomes

- Report can expose `evidence_conversion_power_contract.status=qualified` only when powered evidence, full-stack availability, no gaming flags, verified produced baseline, AEB-0 real artifact, and explicit power assumptions are all satisfied.
- Any benchmark `metric_applyable` field is diagnostic compatibility metadata, not policy applyability, and cannot be handed to policy derivation directly.
- `improvement_claim_allowed` remains false; policy mutation remains false.
- Underpowered reports name target MDE/alpha/power/CI-width gaps.

## Forbidden Outcomes

- Raw `n_good`/`n_bad` thresholds alone satisfy P5.
- Underpowered or unavailable full-stack report is converted into an accepted AutoResearch record.
- Baseline-unavailable rows count as rejects.
- P1 real benchmark status is satisfied without AEB-0.

## First RED Test

Add a public-boundary test that supplies sufficient `min_good`/`min_bad` but omits MDE/alpha/power/CI-width. The test should expect `sample_size_sufficiency.status=underpowered` or `evidence_conversion_power_contract.status=blocked`, with `metric_applyable=false`.

## Minimal GREEN Target

Add the smallest power-contract field set and guard evidence-conversion readiness on that contract and AEB-0 status. Do not treat benchmark `metric_applyable` as policy applyability.

## Ledger Events/Artifacts

- `stage=powered_factorial_benchmark`
- `evidence_kind=diagnostic_report`
- artifact: `powered_factorial_report.json`
- blocked reasons: missing AEB-0, underpowered, missing power assumptions, baseline unavailable, reviewer panel unavailable, gaming flags.

## Acceptance Criteria

- Report includes paired discordant counts, target MDE, alpha, power, CI width, and status.
- Same candidate pool and baseline verification are preserved.
- No improvement or policy claim is emitted by the benchmark report.

## Blocked By

AEB-0 for real-evidence claims; AEB-1 for hardened report-integrity fields.

## AEB-3: Reviewer Independence And Effective-Vote Measurement Gate

## PRD Promise

P3, P4, P7, P10.

## What To Build

Link reviewer diversity/independence metrics to official and powered rows, including reviewer provenance, generator disjointness, pairwise reviewer agreement, pairwise oracle-error overlap, effective-vote estimate, leave-one-reviewer-out effects, and disagreement caveats. This is a measurement gate, not an authority gate.

## Public Boundary For First RED Test

`run_mergeability_reviewer_roster_diagnostic`.

## Chosen Seam/Interface

Reviewer-roster diagnostic report dictionary plus official-row linkage.

## Allowed Outcomes

- Effective-vote status is `computed` only with oracle-grounded reviewer errors and at least two reviewers.
- Fixture-only or saturated evidence remains report-only and blocked for roster selection.
- Same-family decisive votes and unproven provider-family warnings are visible.

## Forbidden Outcomes

- Disagreement-only evidence is called independent signal.
- Zero reviewer errors prove independence.
- Fixture-only ablation drops reviewers or selects Codex-only reviewers.
- Roster/effective-vote output changes `metric_applyable`, `improvement_claim_allowed`, or policy derivation.

## First RED Test

Add a public-boundary roster diagnostic test where reviewers disagree but have zero oracle-grounded errors. The test should require `effective_vote_estimate.status=unavailable`, `oracle_grounded_reviewer_errors_required` in guard reasons, no roster selection authority, and no trust-path flag changes.

## Minimal GREEN Target

Ensure report/linkage fields preserve effective-vote unavailability and guard reasons wherever official/powered evidence references reviewer diversity.

## Ledger Events/Artifacts

- `stage=reviewer_independence_measurement`
- `evidence_kind=diagnostic_report`
- artifact: reviewer roster diagnostic report
- blocked reasons: zero oracle-grounded errors, same-family decisive vote, fixture-only evidence, missing reviewer provenance.

## Acceptance Criteria

- Same candidate pool hash links reviewer metrics to official/powered rows.
- Effective-vote estimate cannot compute without oracle-grounded errors.
- Reviewer public evidence remains public-only and leak-checked.
- Measurement output is not an authority gate.

## Blocked By

AEB-0 for official-row real-evidence linkage. Fixture-shaped diagnostic guard tests may run before AEB-0.

## AEB-4: Benchmark-To-AutoResearch Evidence Conversion Bridge

## PRD Promise

P5, P8, P10.

## What To Build

Introduce an evidence conversion bridge that consumes qualified benchmark-backed evidence only after AEB-0 and power/quality prerequisites pass. It accepts operator review metadata, evaluator execution refs/hashes, empty-floor comparison, evaluator-quality controls, and a candidate policy overlay ref, then emits AutoResearch attempts/reports with `records[]` suitable for policy-evolution derivation.

## Public Boundary For First RED Test

New seam/interface: `promote_benchmark_report_to_autoresearch_report`.

## Chosen Seam/Interface

A deep evidence conversion module interface accepting benchmark report path/payload, candidate overlay ref, evaluator run ref/hash, quality manifest, AEB-0 artifact ref, power contract ref, and operator review metadata.

## Allowed Outcomes

- Accepted records have `metric_source=evaluator_execution`, `evaluator_run_ref`, `evaluator_run_hash`, positive delta, empty-floor win, supervisor-generated evaluator-quality controls, no gaming flags, qualified AEB-0 evidence, and mutation flags false.
- Rejected/blocked records preserve exact reasons.
- Evidence conversion emits report-only AutoResearch summary and ledger entries.

## Forbidden Outcomes

- Directly handing benchmark report dicts to policy derivation.
- Converting underpowered, blocked, no-AEB-0, fixture-only, no-evaluator-hash, no-empty-floor, or no-quality-control evidence into accepted AutoResearch records.
- Mutating policy/default/gate state.

## First RED Test

Add a public-seam test that passes an otherwise powered benchmark report without AEB-0 artifact ref or `evaluator_run_hash`. The test should expect an AutoResearch record with rejected/blocked status, exact blocked reason, no derivable policy record, and ledger status `blocked`.

## Minimal GREEN Target

Implement only enough bridge normalization to emit blocked AutoResearch report records and ledger events for missing AEB-0/evaluator provenance.

## Ledger Events/Artifacts

- `stage=benchmark_to_autoresearch_evidence_conversion`
- `evidence_kind=evaluator_execution`
- artifact: AutoResearch report JSON
- blocked reasons: missing AEB-0, missing evaluator ref/hash, missing empty-floor, missing quality controls, gaming flags, underpowered benchmark, missing operator review, missing overlay candidate.

## Acceptance Criteria

- Bridge output passes AutoResearch report shape with `records[]`.
- Policy derivation returns no proposals for blocked/rejected records.
- Accepted-path tests prove no policy mutation and no gate advancement.

## Blocked By

AEB-0, AEB-2.

## AEB-5: Draft Policy Proposal Gate

## PRD Promise

P8, P9, P10.

## What To Build

Wire accepted bridge-created AutoResearch records into policy-evolution derivation so the system can create draft `.supervisor/policy-overlay.yaml` proposals behind operator approval, while refusing all direct mutation.

## Public Boundary For First RED Test

`derive_policy_evolution_proposals_from_report`.

## Chosen Seam/Interface

Policy-evolution derivation interface.

## Allowed Outcomes

- Accepted records create draft proposals with candidate overlay refs and authority invariants.
- Skipped records write skip events with exact applyability/quality reasons when state/run_id are supplied.
- Operator approval remains required before any filesystem mutation.

## Forbidden Outcomes

- Calling approval/apply path during derivation.
- Proposal without AEB-0 evidence, empty-floor win, or positive delta.
- Proposal with `default_change_allowed`, `policy_mutated`, or `gate_advanced` true.

## First RED Test

Add a public-boundary derivation test using a bridge-style accepted AutoResearch report whose record lacks AEB-0 evidence or `empty_floor_comparison`. The test should expect no proposal and a skipped/blocked derivation reason.

## Minimal GREEN Target

Update bridge-to-policy handoff, not approval/apply behavior, so derivation reasons are operator-visible.

## Ledger Events/Artifacts

- `stage=policy_derivation`
- `evidence_kind=manual_review`
- artifact: draft policy proposal JSON
- blocked reasons: missing AEB-0, applyability error, missing positive delta, missing empty-floor win, missing candidate overlay ref, gaming flags.

## Acceptance Criteria

- Derivation creates proposals only from accepted, applyable AutoResearch records.
- Proposals include candidate overlay ref and authority invariants.
- No policy file changes until explicit approval interface is called by an operator.

## Blocked By

AEB-0, AEB-4.

## AEB-6: Evidence Ledger And Translation Audit

## PRD Promise

P0, P1, P2, P3, P4, P5, P6, P7, P8, P9, P10.

## What To Build

Add a JSONL ledger writer or schema contract for every stage in the benchmark-to-AutoResearch-to-policy proposal chain, plus an audit helper/report that proves every PRD promise has an issue, every issue has a public-boundary RED test, every real-evidence claim depends on AEB-0, and every blocked/report-only state remains explicit.

## Public Boundary For First RED Test

`supervisor_event_ledger` or a new evidence ledger writer seam.

## Chosen Seam/Interface

JSONL ledger record schema `supervisor-auto-evolve-benchmark-ledger/v1`.

## Allowed Outcomes

- Each stage emits one ledger record with artifact path/hash, AEB-0 dependency status, promise IDs, metric source, policy flags, status, blocked reasons, and operator review requirement.
- Translation audit blocks if any promise lacks issue/test coverage or if any real-evidence claim bypasses AEB-0.
- Langfuse/Opik sink and annotation queue remain deferred/out-of-trust-path.

## Forbidden Outcomes

- Report-only or blocked states are omitted.
- Ledger omits artifact hashes, evaluator run hashes, or AEB-0 dependency status.
- Translation audit allows helper-only tests, missing promise coverage, or real-evidence claims without AEB-0.

## First RED Test

Add a public-boundary ledger test that writes one blocked downstream event without AEB-0 and asserts schema version, stage, promise IDs, artifact hash, `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, `operator_review_required=true`, `blocked_reasons=["real_official_all_arms_artifact_required"]`, and deferred sink/annotation status are preserved.

## Minimal GREEN Target

Introduce schema validation and JSONL append for a blocked event only.

## Ledger Events/Artifacts

- All stages listed in `ledger-schema.md`.
- Artifact: `auto_evolve_benchmark_ledger.jsonl`.

## Acceptance Criteria

- JSONL schema validates for all issue stages.
- Audit checklist fails closed on missing promises, helper-only tests, AutoResearch bypass, or missing AEB-0 dependency.
- Operator-facing report names report-only, blocked, underpowered, deferred, and not-yet-proven states.

## Blocked By

None - can start immediately and support every later issue.
