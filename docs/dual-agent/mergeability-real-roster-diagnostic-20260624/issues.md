# Issues

## Issue 1: Add Same-Pool Roster Diagnostic Report

## What to build

Add a report-only mergeability reviewer-roster diagnostic that evaluates S_probe, S_probe plus reviewer-family arms, and S_probe plus full panel over the same candidate pool.

## Acceptance criteria

- [ ] Report contains S_probe, S_probe plus Codex, S_probe plus cross-family text-only reviewer, and S_probe plus full panel arms when matching reviewer receipts exist.
- [ ] Every arm records the same candidate pool hash.
- [ ] Reviewer receipts for unknown candidates are rejected.
- [ ] Report-only invariants remain false.

## Blocked by

None.

## Issue 2: Add Oracle-Grounded Reviewer Independence Analysis

## What to build

Report pairwise reviewer agreement, pairwise oracle-error overlap, and effective-vote status without treating zero-error saturated fixtures as proof of independence.

## Acceptance criteria

- [ ] Zero reviewer oracle errors make effective-vote estimation unavailable.
- [ ] Pairwise oracle-error overlap is recorded when reviewer pairs share candidates.
- [ ] Disagreement without oracle-grounded reviewer errors keeps roster selection blocked.

## Blocked by

Issue 1.

## Issue 3: Gate Roster Selection Authority

## What to build

Add a roster-selection guard for the diagnostic that allows authority only for real or disagreement-enriched evidence with oracle-grounded reviewer errors, and keeps all benchmark outputs report-only.

## Acceptance criteria

- [ ] Fixture-only evidence keeps roster selection blocked.
- [ ] Disagreement-enriched evidence with reviewer oracle errors can mark roster-selection evidence as available while preserving report-only invariants.
- [ ] Same-family generator/reviewer warnings block authority.

## Blocked by

Issue 2.
