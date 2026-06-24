## Problem Statement

The mergeability reviewer panel can now report provenance, but reviewers still return generic accept/revise/deny decisions without a public mergeability rubric or an explicit abstention path. That makes it too easy to interpret a reviewer accept as proof of mergeability even when the reviewer only had weak public evidence.

## Solution

Add `mergeability_rubric/v1` to full-gate reviewer packets and reports. The rubric must be public-assessable, must not ask reviewers to judge hidden-oracle outcomes, and must allow `needs_human_review` as an abstention label. Rubric labels are descriptive reviewer evidence only; the deterministic oracle remains the scoring authority for FAR/TAR.

## User Stories

1. As a reviewer, I want a public mergeability rubric, so that I can label candidate evidence consistently.
2. As a benchmark operator, I want `needs_human_review` to be separate from accept, so that uncertain public evidence does not become a false supervisor accept.
3. As a benchmark operator, I want rubric coverage and abstention coverage reported separately, so that I can see when public evidence is insufficient.
4. As a supervisor maintainer, I want hidden-oracle outcomes excluded from reviewer criteria, so that reviewer judgments remain oracle-isolated.
5. As a benchmark operator, I want FAR/TAR to remain deterministic-oracle grounded, so that LLM labels cannot become the benchmark arbiter.

## PRD Promise Contracts

- P1: Full-gate reviewer packets expose `mergeability_rubric/v1`.
- P2: The rubric contains only public-assessable criteria and does not include hidden-oracle-fail as a reviewer blocker.
- P3: Reviewer results can carry `mergeable`, `not_mergeable`, or `needs_human_review` labels.
- P4: `needs_human_review` is an abstention and is not accepted for FAR/TAR.
- P5: Reports expose rubric label coverage and abstention coverage.
- P6: Report text states that rubric labels are descriptive and deterministic oracle scoring remains the benchmark arbiter.

## Implementation Decisions

- Add the rubric at the full-gate reviewer packet seam.
- Read labels from reviewer result critical-review metadata when present.
- Convert `needs_human_review` into a non-accepting panel result before panel aggregation.
- Keep FAR/TAR arm summaries based on supervisor/full-gate accept booleans and deterministic oracle rows.
- Mirror rubric coverage into both top-level reports and configured-panel blocks.

## Testing Decisions

- Start with a public-boundary packet test asserting the rubric appears in full-gate packets and remains leak-scan clean.
- Add a configured-panel report test where an accepting reviewer returns `needs_human_review` and assert S_full does not accept.
- Add a report test asserting rubric labels are descriptive and oracle agreement remains oracle-grounded.
- Re-run the provenance/guard/dashboard focused set to catch regressions.

## Out of Scope

- Human annotation workflow.
- Maintainer-grade FrontierCode rubric.
- Roster selection.
- Real benchmark execution.
