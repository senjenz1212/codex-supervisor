## Slice 1 - Official All-Arms Diagnostic Wrapper

Priority: high

Scope: Add the official all-arms diagnostic runner and CLI route that reuse official replay inputs, inspect the resulting arms, and write a top-level diagnostic report.

PRD promise: P1, P2, P3, P4

Public boundary: run_official_all_arms_diagnostic through the official replay runner interface and script entrypoint.

Acceptance criteria:

- [ ] The diagnostic status is completed only when official replay status is completed and all four arms are available.
- [ ] Missing baseline, S_full, oracle ceiling, reviewer roster, hidden-isolation, or matched-TAR evidence records explicit unavailable reasons.
- [ ] The report includes n_good, n_bad, FAR/TAR/FRR, matched-TAR status, hidden leak check, and report-only flags.

## Slice 2 - Public Boundary Regression Tests

Priority: high

Scope: Add tests that exercise the diagnostic through official-shaped inputs, fake official-equivalent oracle receipts, produced baseline receipts, and configured reviewer-panel results.

PRD promise: P1, P2, P3, P4

Public boundary: verify_official_all_arms_report via swebench_mergeability_official_all_arms_diagnostic_runner.

Acceptance criteria:

- [ ] A two-good one-bad sample with full reviewer roster and produced baseline receipts reports completed with matched-TAR computed.
- [ ] Oracle unavailable evidence makes the diagnostic unavailable and suppresses FAR/TAR/FRR.
- [ ] Missing S_full reviewer evidence or missing produced baseline receipts keeps the report unavailable and non-applyable.

## Slice 3 - Non-Applyable Evidence Guard

Priority: medium

Scope: Preserve the diagnostic-only policy surface so official all-arm evidence cannot create a policy mutation, default change, or human mergeability claim.

PRD promise: P4

Public boundary: collect_diagnostic_policy_flags from the persisted official all-arms report.

Acceptance criteria:

- [ ] metric_applyable, improvement_claim_allowed, powered_improvement_claim_allowed, human_mergeability_claim_allowed, policy_mutated, and gate_advanced remain false.
- [ ] The report labels all-arms population as insufficient without matched-TAR computation.
- [ ] The nested official replay report remains available for audit without turning the diagnostic into powered evidence.
