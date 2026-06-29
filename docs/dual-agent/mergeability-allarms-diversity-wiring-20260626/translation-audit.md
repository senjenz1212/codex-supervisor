# Translation Audit

## Stage Checklist

- [x] `to-prd` loaded and used to synthesize `prd.md`.
- [x] `codebase-design` loaded and used to keep one report assembly seam.
- [x] PRD grill gate resolved in `grill-findings.md`.
- [x] `to-issues` loaded and used to create an independently verifiable tracer bullet.
- [x] `tdd` loaded and used to plan one RED then one minimal GREEN.
- [x] TDD grill gate resolved in `grill-findings-tdd.md`.
- [x] P1 has an issue claimant.
- [x] P1 names a public boundary and chosen seam.
- [x] The first RED test starts at `_build_official_all_arms_diagnostic_report`.
- [x] The first RED test uses the same seam named in the PRD and issue.
- [x] The issue is a tracer bullet through report assembly, not a horizontal helper-only slice.
- [x] Mocks stay below the public boundary; the three metric helpers are not mocked.
- [x] Forbidden outcomes are represented in the PRD, issue, and TDD plan.
- [x] No autonomous benchmark-to-policy bridge is introduced.
- [x] `prd_review`, `issues_review`, and `tdd_review` are accepted in `ledger.jsonl`.

## Residual Risks

- `_leave_one_reviewer_out_analysis` was originally used by the factorial path, so implementation must carefully adapt official rows without changing that helper.
- Official candidate ids can repeat across instances, so the leave-one adapter must use a unique candidate key.
