# Translation Audit

Task id: `mergeability-discordant-power-20260626`

## Stage Ledger

- `prd_review`: accepted in `ledger.jsonl`.
- `issues_review`: accepted in `ledger.jsonl`.
- `tdd_review`: accepted in `ledger.jsonl`.
- `tri_agent_review`: revised the packet; second PRD/issues/TDD records accepted in `ledger.jsonl`.

## Checklist

- [x] Current child skills were loaded for `to-prd`, `codebase-design`, `grill-with-docs`, `to-issues`, and `tdd`.
- [x] Every PRD promise has an issue claimant.
- [x] Every promise names a public boundary and chosen seam.
- [x] `grill-findings.md` exists and all findings are resolved.
- [x] `grill-findings-tdd.md` exists and all findings are resolved.
- [x] Every issue has a `PRD promise` block.
- [x] Every issue names a first public-boundary RED test.
- [x] First RED test starts at `run_powered_factorial_mergeability_evaluation`, not a helper.
- [x] TDD plan follows one RED, one minimal GREEN.
- [x] No silent mocking above the public boundary.
- [x] Forbidden outcomes appear in the PRD, issues, and TDD plan.
- [x] Authority flags stay false.
- [x] Docker, live calls, oracle adapter changes, candidate changes, baseline changes, panel changes, and the autonomous benchmark-to-policy bridge are out of scope.
- [x] Tri-agent revise/block findings were recorded, folded into `issues.md` and `tdd.md`, and resolved before implementation.
- [x] TDD now pins sparse nonzero discordance, chi-square threshold behavior, FRR interval projection, zero-event FAR/TAR/FRR, and non-Wald interval provenance.

## Promise Coverage

P1 is covered by Issues 1, 2, 3, 4, 5, 9, 10, and 11.

P2 is covered by Issues 6, 7, 8, 9, and 11.

## Residual Risks

- Existing callers may read old interval object keys, so new compact rate objects must be additive.
