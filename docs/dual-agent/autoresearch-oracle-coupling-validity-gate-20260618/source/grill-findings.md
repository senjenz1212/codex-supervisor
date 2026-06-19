# PRD Grill Findings

### Finding 1: Oracle Ceiling Must Remain Useful Without Becoming Evidence Of Improvement

Status: resolved

The PRD correctly separates calibration value from improvement evidence. The implementation must preserve the existing per-task rows, receipts, and exported artifacts so the current pilot can still be replayed, while adding metadata that makes the report non-applyable for improvement claims.

Resolution: Add report-level `metric_applyable=false`, `improvement_claim_allowed=false`, and `report_label=oracle_upper_bound` for the current oracle-derived arm. Preserve report-only invariants and exported JSON artifacts.

### Finding 2: Declared Provenance Should Be Primary, Equality Should Be A Fallback Warning

Status: resolved

Exact treatment-oracle equality is useful for detecting suspicious reports, but it can produce false alarms on very small or genuinely perfect corpora. The stronger signal is declared decision-source provenance showing that the treatment arm used `final_score`, hidden grader output, expected outcome, or another oracle source.

Resolution: Block declared oracle-coupled treatment sources. Add a separate suspicious equality flag that does not replace provenance requirements.

### Finding 3: Policy Derivation Must Consume The New Validity Fields

Status: resolved

The report metadata matters only if downstream policy proposal derivation honors it. The PRD needs an explicit promise that proposal derivation rejects oracle-coupled, non-applyable, or improvement-claim-disabled records.

Resolution: P3 now requires policy derivation rejection for these fields, and the TDD plan includes a public derivation-boundary test.

### Finding 4: Slice Boundary Should Not Hide A Real Supervisor Gate Implementation

Status: resolved

Replacing oracle coupling with actual gate execution is larger than this repair because it requires isolating hidden tests from the candidate-facing acceptance path. Doing both in one slice would blur whether the validity gate itself works.

Resolution: Keep real independent Supervisor acceptance out of scope and record it as the next dependent slice.
