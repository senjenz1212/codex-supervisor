# Issues

## Issue 1: Add real-benchmark evidence fields to official all-arms report

## What to build

Extend the official all-arms diagnostic with oracle limitation, decision-freeze proof, S_probe-vs-S_full marginal, and report-only real-benchmark aliases.

## Acceptance criteria

- [x] Report states SWE-bench is a held-out test-pass proxy, not maintainer mergeability.
- [x] Report includes freeze-before-oracle proof.
- [x] Report includes false-accept reduction at matched true-accept and S_probe-vs-S_full marginal.
- [x] Report-only flags remain false.

## Blocked by

None.

## Issue 2: Add reviewer provenance, rubric, abstention, and self-preference fields

## What to build

Normalize official replay reviewer results into the existing mergeability reviewer evidence helpers and expose the results on the official all-arms report.

## Acceptance criteria

- [x] Report includes reviewer provenance.
- [x] Report includes abstention coverage and descriptive-only rubric scoring authority.
- [x] Report includes generator disjointness and self-preference warnings.
- [x] LLM reviewer labels do not become benchmark oracle labels.

## Blocked by

Issue 1.
