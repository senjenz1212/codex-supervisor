# Harness v1 Forbidden Claims

These statements are forbidden unless the report's evidence bundle reaches the
listed `ClaimGate` level. Similar wording and registered claim IDs are governed
the same way.

| Claim | Minimum level | Why it is currently forbidden for report-only artifacts |
|---|---:|---|
| "Supervisor improves outcomes.", "Arm B is better than Arm C.", or equivalent B-vs-C superiority prose | L3 | Fixture, replay, calibration, and shared-candidate mergeability reports are not randomized powered B-vs-C task-efficacy experiments. |
| "The improvement generalizes across strata." | L4 | One task family, one pooled result, or fewer than three pinned model families (including one optimizer-unseen family) does not establish portability. |
| "The supervisor has positive ROI" or "pays for itself." | L5 | Outcome evidence without measured operating cost cannot establish ROI. |
| "The system safely auto-improves." | L6 | Auto-improvement additionally requires frozen control, sealed holdout, and a passing canary. |

An L2 report may say that a frozen result passed an independent hidden
verifier. It may not convert that outcome statement into a causal improvement
claim.

## Validator Contract

`ClaimGate.validate_report(report, evidence_bundle)`:

- rejects a registered claim whose minimum level exceeds the derived level;
- rejects an `asserted_claim_level` above the derived level;
- accepts registered claim IDs only in the governed `claims` field and
  explicit claim-bearing fields; canonical display text comes from the
  registry;
- never trusts a report-provided `required_level`;
- reports both the required and available levels in the error.

`ClaimGate.derive_report(report, evidence_bundle)` also rejects either managed
improvement flag anywhere in producer input. It then injects both flags and a
claim-gate receipt derived from the evidence bundle.

Current source tests additionally scan production Python under `supervisor/`
and `scripts/` and fail if either managed flag is assigned a literal boolean.
Tests may deliberately construct manual flags to verify rejection.
