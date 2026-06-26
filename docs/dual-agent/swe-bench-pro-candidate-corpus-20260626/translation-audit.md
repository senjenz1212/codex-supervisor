# Translation Audit

Task id: `swe-bench-pro-candidate-corpus-20260626`

## Stage Evidence

- `prd-to-tdd`: loaded and applied.
- `to-prd`: synthesized `prd.md`.
- `codebase-design`: used to keep the loader and generator as explicit seams.
- `grill-with-docs`: produced `grill-findings.md` and `grill-findings-tdd.md`.
- `to-issues`: produced `issues.md`.
- `tdd`: produced `tdd.md` with one-RED-then-minimal-GREEN cycles.

## Promise Coverage

- P1 is covered by issues 1, 2, 3, 7, 8, 10, and 11.
- P2 is covered by issues 4, 5, 6, 8, 9, 10, and 11.

## Boundary Audit

- The first RED test uses `_load_official_predictions(...)`.
- The generator/binning RED test uses the candidate-corpus builder seam.
- Mocks are below the public boundary: solver/oracle are injected dependencies, not hidden labels.
- Forbidden outcomes are represented in tests and blocked artifact rules.

## Gate Statuses

- `prd_review`: accepted.
- `issues_review`: accepted.
- `tdd_review`: accepted.

## Authority Flags

All false: `metric_applyable`, `improvement_claim_allowed`, `powered_improvement_claim_allowed`, `human_mergeability_claim_allowed`, `default_change_allowed`, `policy_mutated`, `gate_advanced`.
