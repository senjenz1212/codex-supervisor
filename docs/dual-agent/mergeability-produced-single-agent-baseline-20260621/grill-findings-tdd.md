## Findings

1. Resolved: The first planned test hits `run_paired_acceptance_pilot`, so the TDD plan starts at the public report boundary rather than a private helper.
2. Resolved: Receipt validation is tested through observable unavailable rows and produced-baseline summaries, avoiding implementation-shape assertions.
3. Resolved: The plan includes a negative comparison case so unavailable baseline evidence cannot slip into matched-TAR arithmetic.
4. Resolved: The plan covers the report-only invariant and policy proposal derivation, which are the user-visible safety outcomes for calibration evidence.
5. Resolved after independent review: a valid-looking receipt with `decision_source=candidate_self_report` must remain unavailable, because metadata self-report evidence cannot be relabeled as produced baseline evidence.

## Stage Verdict

pass
