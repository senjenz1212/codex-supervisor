# Translation Audit Checklist

## Child Skills Loaded

- [x] `prd-to-tdd`
- [x] `to-prd`
- [x] `codebase-design`
- [x] `grill-with-docs`
- [x] `to-issues`
- [x] `tdd`
- [x] `grill-with-docs` again for TDD audit

## Promise Coverage

- [x] P0 covered by AEB-0, AEB-4, AEB-5, AEB-6.
- [x] P1 covered by AEB-0, AEB-1, AEB-2, AEB-6.
- [x] P2 covered by AEB-0, AEB-1, AEB-2, AEB-6.
- [x] P3 covered by AEB-3, AEB-6.
- [x] P4 covered by AEB-3, AEB-6.
- [x] P5 covered by AEB-2, AEB-4, AEB-6.
- [x] P6 covered by AEB-0, AEB-1, AEB-2, AEB-6.
- [x] P7 covered by AEB-0, AEB-1, AEB-3, AEB-6.
- [x] P8 covered by AEB-4, AEB-5, AEB-6.
- [x] P9 covered by AEB-5, AEB-6.
- [x] P10 covered by AEB-6 and the deferred sink prompts.

## AEB-0 Root Gate

- [x] AEB-0 exists before AEB-1 in the issue pack.
- [x] AEB-0 is the root dependency for real benchmark evidence.
- [x] AEB-1+ may run fixture-shaped guardrail tests, but cannot satisfy P1 real-evidence readiness without AEB-0.
- [x] Bridge, sink, annotation queue, and policy proposal readiness are blocked without AEB-0.
- [x] Missing CLI prerequisites are represented as blocked AEB-0 outcomes rather than silent pre-artifact exits.
- [x] Serious real benchmark claims are pinned to SWE-bench Pro or held-out equivalent; Verified is smoke-only unless explicitly justified.

## Issue Requirements

- [x] Every issue has a `PRD Promise` block.
- [x] Every issue names a public boundary or chosen seam/interface.
- [x] Every issue has allowed outcomes.
- [x] Every issue has forbidden outcomes.
- [x] Every issue has a first RED test.
- [x] Every issue has a minimal GREEN target.
- [x] Every issue lists ledger events/artifacts.
- [x] Every issue lists acceptance criteria.
- [x] Every downstream issue names whether it is blocked by AEB-0 for real-evidence claims.

## TDD Requirements

- [x] AEB-0 first RED tests cover missing artifact, missing baseline receipt, missing official oracle receipt, missing CLI prerequisites, Verified smoke-only framing, and false authority flags.
- [x] Oracle bucket tests distinguish empty-but-present buckets from missing/malformed buckets.
- [x] Official all-arms rows expose or link reviewer diversity metrics and blocked reasons.
- [x] Reviewer agreement without oracle-grounded reviewer errors cannot compute effective vote or roster authority.
- [x] First RED tests hit public boundaries or the new bridge seam.
- [x] Helper-only tests are not first tests.
- [x] Plan follows one RED -> one GREEN -> repeat.
- [x] Forbidden outcomes are represented in tests.
- [x] Mocks stay below public boundaries.

## Grill Gates

- [x] PRD grill findings created in `grill-findings.md`.
- [x] PRD grill findings resolved into PRD/issues.
- [x] TDD grill findings created in `grill-findings-tdd.md`.
- [x] TDD grill findings resolved into TDD/issues.

## AutoResearch Bypass Gate

- [x] Benchmark reports remain diagnostic/report-only.
- [x] Benchmark-backed evidence conversion bridge is the only benchmark-to-AutoResearch conversion path.
- [x] Policy evolution consumes accepted AutoResearch records, not raw benchmark reports.
- [x] Draft proposals require operator approval before mutation.
- [x] Accepted bridge-path tests require AEB-0-qualified evidence.

## Observability And Annotation Deferral

- [x] Langfuse/Opik sink is explicitly out of the benchmark trust path.
- [x] Langfuse/Opik sink depends on AEB-0 artifact or blocked artifact before ingest.
- [x] Sink output cannot affect `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `policy_mutated`, or `gate_advanced`.
- [x] Sink traces, scores, annotations, or dashboard URLs cannot satisfy evaluator provenance, quality controls, empty-floor win, effective-vote, roster selection, or policy derivation requirements.
- [x] Maintainer-merge annotation queue is deferred until after the read-only sink boundary is proven.

## Blockers / Assumptions

- Full live SWE-bench benchmark execution is out of scope for this packet revision.
- Real official harness smoke exists, but real all-arms diagnostic evidence is not yet committed/proven.
- Official oracle missing/malformed status bucket handling must be fixed before any live evidence claim; empty-but-present buckets may remain pass-compatible.
- No external issue tracker was used; issues are published as local markdown in this packet.
- Subagents were spawned for validation and intentionally left open.
- Current repo worktree was already dirty before this planning packet; unrelated existing changes were not touched.
- AEB-2 intentionally calls out a current powering-contract gap: raw `min_good`/`min_bad` is not enough for P5.
