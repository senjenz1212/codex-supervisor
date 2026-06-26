Supervisor Workflow Revision Result

## 1. Summary Verdict

The packet has been revised so **AEB-0: Real Official All-Arms Artifact Gate** is the root dependency for real benchmark evidence. Downstream bridge, power, sink, annotation, and policy-proposal work may still develop fixture-shaped guardrail tests, but cannot claim real-evidence readiness until AEB-0 produces a real `official_all_arms_diagnostic_report.json` or a blocked artifact with exact unavailable reasons.

Benchmark reports remain diagnostic/report-only. Langfuse/Opik sink work is deferred into a later read-only packet and is explicitly outside the trust path. The maintainer-merge annotation queue is deferred until after the sink boundary is proven.

## 2. Files Revised

- `prd.md`
- `issues.md`
- `tdd.md`
- `ledger-schema.md`
- `translation-audit.md`
- `grill-findings.md`
- `grill-findings-tdd.md`
- `tri-agent-validation.md`
- `tri-agent-findings.md`
- `next-supervisor-prompts.md`

## 3. AEB-0 Definition

AEB-0 requires:

- A real `official_all_arms_diagnostic_report.json`, or a blocked artifact with exact unavailable reasons.
- Dataset pinned to SWE-bench Pro or held-out equivalent for serious claims; Verified is smoke-only unless explicitly justified.
- Produced single-agent baseline receipt present and verified.
- Official oracle receipt present and verified.
- S_probe and S_full evidence present or explicitly unavailable.
- Reviewer packet/public evidence present or explicitly unavailable.
- Hidden-field/protected-material leak check scoped to public/reviewer/generator/frozen/public packet surfaces.
- Empty-but-present `FAIL_TO_PASS` / `PASS_TO_PASS` buckets may pass; missing or malformed buckets block/unavailable.
- All authority flags false.

## 4. Dependency Graph

1. AEB-0 Real Official All-Arms Artifact Gate.
2. AEB-1 Official All-Arms Evidence Packet Hardening, blocked by AEB-0 for real-evidence claims.
3. AEB-2 Powered Factorial Statistical Contract, blocked by AEB-0 and AEB-1 for real-evidence claims.
4. AEB-3 Reviewer Independence And Effective-Vote Measurement Gate, blocked by AEB-0 for official-row real-evidence linkage.
5. AEB-4 Benchmark-To-AutoResearch Evidence Conversion Bridge, blocked by AEB-0 and AEB-2.
6. AEB-5 Draft Policy Proposal Gate, blocked by AEB-0 and AEB-4.
7. AEB-6 Evidence Ledger And Translation Audit, can start immediately and enforces the graph.

Deferred:

- Langfuse/Opik read-only sink slice, only after AEB-0 artifact exists.
- Maintainer-merge annotation queue, only after sink boundary is proven.

## 5. PRD Changes

See `prd.md`.

The PRD now includes P0 for the root all-arms artifact gate, dataset pinning, Verified-smoke-only framing, sink-out-of-trust-path framing, and annotation deferral.

## 6. Issue Changes

See `issues.md`.

The issue pack now starts with AEB-0 and updates AEB-1 through AEB-6 to preserve AEB-0 dependency status.

## 7. TDD Changes

See `tdd.md`.

TDD now starts with AEB-0 RED tests for missing artifact, missing CLI prerequisites, missing baseline/oracle receipts, Verified smoke-only framing, and false authority flags. It also adds the requested oracle bucket distinction and sink-trust-path guard.

## 8. Translation Audit Changes

See `translation-audit.md`.

The audit now verifies AEB-0 exists, all real-evidence claims depend on it, Langfuse/Opik is out of the trust path, and annotation queue work is deferred.

## 9. Tri-Agent Findings

See `tri-agent-findings.md`.

The new validation agents confirmed the required packet changes: AEB-0 as root gate, explicit CLI pre-artifact blockers, empty-present vs missing/malformed oracle bucket framing, sink-blind trust path, and AEB-0-qualified bridge inputs.

## 10. Remaining Blockers

- No real AEB-0 artifact has been produced by this packet revision.
- Official oracle missing/malformed bucket handling still needs implementation.
- Real benchmark claims remain blocked until AEB-0 succeeds or emits a blocked artifact.
- Langfuse/Opik sink work remains deferred.
- Maintainer-merge annotation queue remains deferred.

## 11. Next Command / Next Prompt

See `next-supervisor-prompts.md`.

Run Prompt 1 next to implement/run AEB-0. Run the Langfuse/Opik sink prompt only after AEB-0 has produced a real or blocked artifact to ingest.

## Load-Bearing Repo Evidence

- Official all-arms diagnostic is diagnostic/report-only and records SWE-bench oracle limitation: `supervisor/swe_bench_mergeability.py:2575`, `supervisor/swe_bench_mergeability.py:2916`, `supervisor/swe_bench_mergeability.py:2944`.
- Official all-arms writes `official_all_arms_diagnostic_report.json`: `supervisor/swe_bench_mergeability.py:2643`.
- CLI official replay/all-arms gates require dataset fetch permission, dataset, predictions, and oracle adapter: `supervisor/swe_bench_mergeability_cli.py:109`.
- Official oracle compatibility is real, but malformed missing status buckets currently need fail-closed coverage: `supervisor/swe_bench_official_oracle.py:86`, `supervisor/swe_bench_official_oracle.py:177`, `supervisor/swe_bench_official_oracle.py:283`.
- Reviewer roster diagnostics require oracle-grounded reviewer error evidence for effective-vote/selection claims: `supervisor/mergeability_bench.py:5254`, `supervisor/mergeability_bench.py:5297`.
- Policy derivation requires accepted AutoResearch records with evaluator provenance, quality controls, positive delta, empty-floor win, and overlay candidate refs before creating draft proposals: `supervisor/autoresearch/policy_evolution.py:76`, `supervisor/autoresearch/policy_evolution.py:522`, `supervisor/autoresearch/policy_evolution.py:547`, `supervisor/autoresearch/policy_evolution.py:665`, `supervisor/autoresearch/policy_evolution.py:694`, `supervisor/autoresearch/policy_evolution.py:729`.
