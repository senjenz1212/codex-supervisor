### Finding 1

status: resolved

The TDD plan could have started with helper-only packet tests and missed the report contract. Resolution: every planned test names `run_paired_acceptance_pilot` as the public boundary, with packet helpers exercised only after the report-level behavior is red.

### Finding 2

status: resolved

The unavailable-reviewer case could accidentally weaken reviewer authority by falling back to public checks. Resolution: `test_full_gate_unavailable_reviewer_does_not_count_as_accept` requires unavailable panel output to make the full-gate metric unavailable rather than accepted.

### Finding 3

status: resolved

The metric plan could report a panel marginal delta even when true-accept rates are not matched. Resolution: `test_panel_marginal_delta_is_reported_only_when_matched_true_accept_is_computable` requires a structured unavailable reason unless matched true-accept conditions are satisfied.
