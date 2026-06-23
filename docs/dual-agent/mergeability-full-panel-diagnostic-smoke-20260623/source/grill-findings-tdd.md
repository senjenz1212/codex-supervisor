# TDD Grill Findings

### Finding 1: Public Boundary Test Comes First

Status: resolved

The first test must hit the diagnostic public boundary, not a helper-only packet builder. The TDD plan begins with the report-writing configured full-panel smoke.

### Finding 2: Panel Marginal Can Be Diagnostic

Status: resolved

Panel marginal computation is not required to be computed on this small corpus. The test accepts computed, unavailable, not_matched, or insufficient status only when a concrete reason is present.

### Finding 3: Availability Differs From Quality

Status: resolved

Reviewer unavailability and panel quality rejection must remain distinct. Tests check missing verdict and infrastructure cases separately from quality reject behavior.

### Finding 4: Report-Only Flags Are Direct Assertions

Status: resolved

Report-only invariants must be asserted directly. The TDD plan includes explicit false checks for metric, improvement, default change, policy mutation, and gate advancement flags.
