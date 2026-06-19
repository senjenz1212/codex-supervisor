## Findings

### Finding 1: Helper-Only Drift

Status: resolved

The tests could become helper-only if they target metadata parsers directly. Resolution: every named test starts at `run_paired_acceptance_pilot` or `validate_mergeability_corpus`.

### Finding 2: Vacuous No-Regression Pass

Status: resolved

The no-regression test could pass vacuously if the fixture never had a previously passing behavior. Resolution: the TDD plan requires a candidate that explicitly regresses a behavior that the base or known-good control proves passing.

### Finding 3: Report-Only Guard Duplication

Status: resolved

The report-only guard could duplicate existing tests without touching held-out data. Resolution: the named report-only test must use the held-out and no-regression report path.

### Finding 4: Weak Replayability Assertion

Status: resolved

Replayability could be asserted with path existence only. Resolution: the artifact test requires deterministic hashes for coverage and no-regression findings.

## Waivers

No waivers. These are blocking TDD constraints for implementation.
