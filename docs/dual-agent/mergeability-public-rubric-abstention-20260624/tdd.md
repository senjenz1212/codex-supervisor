# TDD Plan

## Cycle 1: Packet Rubric Boundary

RED: Add a public-boundary test asserting full-gate reviewer packets contain `mergeability_rubric/v1` and pass leak scanning.

GREEN: Add a public rubric helper to the reviewer packet builder.

## Cycle 2: Abstention Blocks Accept

RED: Add a configured-panel test where a reviewer returns `needs_human_review` while otherwise accepting, and assert S_full does not accept.

GREEN: Normalize `needs_human_review` into a non-accepting reviewer result before panel aggregation.

## Cycle 3: Rubric Coverage Reporting

RED: Add a report test asserting label coverage, abstention count, and deterministic scoring authority fields.

GREEN: Add shared rubric coverage aggregation and include it in paired and bounded reports.

## Cycle 4: Regression Focus Set

RED: Re-run provenance, roster guard, public dashboard, and rubric focused tests.

GREEN: Preserve report-only and oracle-isolation invariants.
