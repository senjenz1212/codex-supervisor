# TDD Grill Findings

### Finding 1: Helper-Only Predicate Tests Would Miss the Public Contract

status: resolved

All level tests call `ClaimGate.max_claim_level`; no test invokes private
predicate helpers.

### Finding 2: A True-Only Manual Override Test Is Too Narrow

status: resolved

The producer seam rejects either managed field regardless of value and at any
nesting depth. The required true-without-L3 case and a nested powered-field
case are both covered.

### Finding 3: L1 Refusal Must Exercise an Actual L3 Assertion

status: resolved

The suite tests both the forbidden causal phrase and
`asserted_claim_level: L3` against a fixture-replay L1 bundle.

### Finding 4: Producer Migration Needs a Regression Beyond Spot Checks

status: resolved

An AST scan covers all production Python under `supervisor/` and `scripts/`,
while focused producer tests preserve report shapes and false authority.

### Finding 5: Runtime and YAML Registries Could Drift

status: resolved

The focused documentation test parses the ladder and claims YAML and compares
ordered levels, managed-output thresholds, claim IDs, and required levels with
the runtime registry.

## Residual Risk

Legacy report-only producers use `ClaimGate.derived_claim_flags()` without a
standardized evidence bundle because those report schemas predate PROGRAM-001.
This is deliberately conservative: they remain below L3. New efficacy
producers should use `derive_report` with a complete evidence bundle.
