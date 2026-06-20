## Findings

1. **Helper-only risk resolved.** Every named test starts at `validate_mergeability_corpus` or `run_paired_acceptance_pilot`, the same public interfaces used by the bench runner and reports.

2. **Vacuous no-regression risk resolved.** The no-regression test must include both a prior oracle-positive rejection and a prior oracle-negative false accept, proving the rule blocks only the intended case.

3. **Reporting honesty risk resolved.** The interval and best-of-K tests inspect the public report fields rather than helper calculations, so user-visible output carries the guarantee.

4. **Policy authority risk resolved.** The report-only test keeps derivation and gate authority out of scope while still validating the new reporting path.

## Resolution

The TDD plan is accepted. Implementation must not add a new external seam unless the existing mergeability bench interface cannot expose the promised evidence.
