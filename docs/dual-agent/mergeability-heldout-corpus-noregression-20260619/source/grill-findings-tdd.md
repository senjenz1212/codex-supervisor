### Finding 1: Helper-only risk

Status: resolved

Every named test starts at `validate_mergeability_corpus` or `run_paired_acceptance_pilot`, the same public interfaces used by the bench runner and reports.

### Finding 2: Vacuous no-regression risk

Status: resolved

The no-regression test includes both a prior oracle-positive rejection and a prior oracle-negative false accept, proving the rule blocks only the intended case.

### Finding 3: Reporting honesty risk

Status: resolved

The interval and best-of-K tests inspect the public report fields rather than helper calculations, so user-visible output carries the guarantee.

### Finding 4: Policy authority risk

Status: resolved

The report-only test keeps derivation and gate authority out of scope while still validating the new reporting path.
