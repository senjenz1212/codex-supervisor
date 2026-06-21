## Public Boundary

The first RED test calls `run_paired_acceptance_pilot` without produced baseline receipts and asserts the public report exposes `single_agent_baseline` as unavailable while keeping the metadata baseline separate.

## TDD Plan

1. RED: Add a paired-pilot boundary test proving missing produced baseline decisions make `single_agent_baseline` unavailable and add a baseline evidence flag. GREEN: Add optional baseline decision input and unavailable row fields.
2. RED: Add a paired-pilot boundary test proving replayed decision receipts populate `single_agent_baseline` FAR, TAR, producer, prompt hash, candidate id, and artifact hash separately from `metadata_accept_all_baseline`. GREEN: Resolve receipts through the shared baseline decision normalizer.
3. RED: Add a paired-pilot boundary test proving matched-TAR refuses unavailable produced baseline comparisons. GREEN: Make matched-TAR status report unavailable when either compared arm has unavailable evidence.
4. RED: Add a paired-pilot boundary test proving metadata accept-all or `candidate_self_report` receipt sources are not renamed as produced evidence. GREEN: Restrict available produced rows to trusted produced or replayed baseline decision sources.
5. RED: Add a paired-pilot boundary test proving report-only invariants block policy proposals. GREEN: Preserve compatibility aliases and report-only guardrails.

## Boundary Assertions

The tests assert report keys, arm summaries, per-task rows, exported JSON content, and policy derivation outcomes. Helper validation is acceptable only to support the public-boundary tests and must not replace them.
