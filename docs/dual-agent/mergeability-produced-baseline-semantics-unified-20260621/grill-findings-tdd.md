# TDD Grill Findings

## Finding 1: The First Test Must Cross The Runner Boundary

Status: resolved

The first TDD test is not a helper test. It runs the SWE-bench fixture runner and inspects the produced bridge report, which exercises manifest candidate handling, public packets, frozen decisions, oracle ordering, and arm summaries together.

## Finding 2: Valid Receipts Need Positive Coverage

Status: resolved

Only testing missing receipts would make the implementation too easy to satisfy by marking every baseline unavailable. The second test proves a real produced receipt becomes available and preserves provenance fields.

## Finding 3: Live Path Must Stay Honest

Status: resolved

The live test verifies patch generation alone does not synthesize baseline acceptance. This guards the budget-gated path from silently reintroducing accept-all behavior after replay semantics are repaired.
