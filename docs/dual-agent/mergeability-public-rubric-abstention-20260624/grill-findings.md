# PRD Grill Findings

## Finding 1: The Rubric Must Not Ask Reviewers To Judge Hidden Outcomes

Status: resolved.

The rubric is public-assessable only. It asks reviewers to mark insufficient public evidence as `needs_human_review` instead of guessing hidden outcomes.

## Finding 2: Abstention Must Affect Acceptance, Not Just Reporting

Status: resolved.

The implementation converts `needs_human_review` into a non-accepting reviewer result before panel aggregation, so it cannot accidentally count as an accepted full-gate row.

## Finding 3: LLM Labels Must Not Become The Benchmark Arbiter

Status: resolved.

The report carries a coverage block that explicitly labels rubric output as descriptive and keeps deterministic oracle scoring as the FAR/TAR authority.
