# Tri-Agent Findings

The three validation agents were spawned exactly once for the AEB-0 revision and left open. They performed read-only audits.

## Agent A: Dependency And AutoResearch Gate Auditor

Verdict: the previous packet was directionally right but had not actually made AEB-0 the root gate. The revised packet now does.

Key findings incorporated:

- AEB-0 must be inserted before AEB-1 in `issues.md`, `tdd.md`, `index.md`, and `translation-audit.md`.
- Translation audit must not mark P1 satisfied by downstream issues without AEB-0.
- AEB-2 wording must avoid treating benchmark `metric_applyable` as policy applyability. The revised packet uses `evidence_conversion_power_contract.status=qualified` language and states benchmark `metric_applyable` is not policy applyability.
- AEB-4 accepted-path tests must require AEB-0-qualified evidence and AutoResearch validation/report shape.
- AEB-5 remains derivation-only and must not call approval/apply.
- Oracle bucket wording must distinguish empty-present from missing/malformed.

Repo anchors:

- Official all-arms false authority flags: `supervisor/swe_bench_mergeability.py:2944`.
- Powered factorial still keeps `improvement_claim_allowed=false`: `supervisor/mergeability_bench.py:2051`.
- Policy derivation reads AutoResearch `records[]` and blocks on report applyability: `supervisor/autoresearch/policy_evolution.py:76`, `supervisor/autoresearch/policy_evolution.py:500`.
- Record-level derivation requires evaluator provenance and quality controls: `supervisor/autoresearch/policy_evolution.py:522`, `supervisor/autoresearch/policy_evolution.py:547`.

## Agent B: SWE-Bench / All-Arms Artifact Auditor

Verdict: AEB-0 is necessary and the official all-arms runner is the right boundary.

Key findings incorporated:

- `swebench_mergeability_official_all_arms_diagnostic_runner` writes `official_all_arms_diagnostic_report.json` and blocks completion on missing baseline, S_probe, S_full, oracle ceiling, reviewer roster, min good/bad, leak check, suppressed metrics, and matched TAR.
- CLI pre-run gates must be represented as blocked AEB-0 outcomes: missing dataset fetch, dataset, predictions, oracle adapter, or supported adapter kind.
- Produced baseline receipt gates are already fail-closed and should be required by AEB-0.
- Empty-but-present oracle buckets may pass; missing/non-mapping `FAIL_TO_PASS` or `PASS_TO_PASS` buckets must become unavailable/blocked.
- Leak wording must scope to public/reviewer/generator/frozen/public packet surfaces, not hidden oracle manifests that exist by design.
- Serious AEB-0 runs should be pinned to SWE-bench Pro or held-out equivalent; Verified is smoke-only.

Repo anchors:

- All-arms runner and artifact write: `supervisor/swe_bench_mergeability.py:2575`, `supervisor/swe_bench_mergeability.py:2643`.
- All-arms availability gates: `supervisor/swe_bench_mergeability.py:2782`, `supervisor/swe_bench_mergeability.py:2810`.
- CLI gates: `supervisor/swe_bench_mergeability_cli.py:109`.
- Baseline receipt copy/normalization: `supervisor/swe_bench_mergeability.py:2428`, `supervisor/mergeability_bench.py:4730`.
- Oracle adapter parse/status issue: `supervisor/swe_bench_official_oracle.py:177`, `supervisor/swe_bench_official_oracle.py:283`.
- Oracle receipt validation: `supervisor/swe_bench_mergeability.py:3210`, `supervisor/swe_bench_mergeability.py:3268`, `supervisor/swe_bench_mergeability.py:3317`.
- Leak checks: `supervisor/swe_bench_mergeability.py:51`, `supervisor/swe_bench_mergeability.py:3409`, `supervisor/swe_bench_mergeability.py:3446`.

## Agent C: Observability / Sink Isolation Auditor

Verdict: revise before sink work. The trust path was clean because no sink existed, but sink isolation had to be codified.

Key findings incorporated:

- Langfuse/Opik is not part of this packet's implementation issues.
- Sink work must be a separate later packet, read-only, and dependent on AEB-0 artifact or blocked artifact.
- Sink output must not affect authority flags, evaluator provenance, effective vote, roster selection, or policy derivation.
- Required future sink tests are listed in `next-supervisor-prompts.md` and the translation audit.

Repo anchors:

- Official all-arms false authority flags: `supervisor/swe_bench_mergeability.py:2944`.
- Policy derivation is sink-blind and consumes AutoResearch records: `supervisor/autoresearch/policy_evolution.py:76`, `supervisor/autoresearch/policy_evolution.py:500`, `supervisor/autoresearch/policy_evolution.py:522`.
- Roster/effective-vote computation is sink-blind and requires oracle-grounded reviewer errors: `supervisor/mergeability_bench.py:2208`, `supervisor/mergeability_bench.py:5254`, `supervisor/mergeability_bench.py:5297`.
