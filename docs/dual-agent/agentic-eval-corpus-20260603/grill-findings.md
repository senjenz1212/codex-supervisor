# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 453353 `prd_review`: shasum of PRD not re-derived (Bash approval denied); verified by reading file at manifest path instead
- event_id 453353 `prd_review`: P4 seed corpus at floor of range (exactly 8 of 8-12 cases)
- event_id 453353 `prd_review`: P3 miner 'balanced sample of clean accepts' is not quantified in the PRD
- event_id 453354 `prd_review`: both agents accepted
- event_id 453486 `issues_review`: both agents accepted
- event_id 453847 `tdd_review`: both agents accepted
- event_id 454100 `implementation_plan`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 454138 `implementation_plan`: NIT: plan Files-To-Touch list omits corpus_evidence/cassettes/ and docs skill-receipts.json though both are realized and test-validated; not a planning defect
- event_id 454138 `implementation_plan`: pytest/shasum un-run (deferred to execution gate; approval pattern)
- event_id 454415 `implementation_plan`: both agents accepted
- event_id 454472 `execution`: test-evidence.md:8 claims '8 passed' but tree has 9 test functions (doc staleness, non-blocking - source has more coverage not less)
- event_id 454472 `execution`: pytest not executed in this gate (approval prompt declined); reliance on test-evidence.md claims for runtime pass/fail
- event_id 454473 `execution`: both agents accepted
- event_id 454927 `outcome_review`: both agents accepted
