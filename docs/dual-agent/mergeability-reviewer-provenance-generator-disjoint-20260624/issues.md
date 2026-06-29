# Issues

## Issue 1: Expose Reviewer Provenance In Mergeability Reports

PRD promise: P1, P2, P3, P4.

Public boundary for first RED test: `run_paired_acceptance_pilot`.

Acceptance criteria:

- Per-reviewer arms include provider family, lineage, tool access, assurance grade, and command-evidence status.
- Configured panel report includes aggregate reviewer provenance.
- Cursor default reviewers are marked unproven cross-family evidence.
- LiteLLM structured reviewers are marked text-only without command evidence.

Blocked by: None.

## Issue 2: Add Generator-Disjointness And Self-Preference Warnings

PRD promise: P5.

Public boundary for first RED test: `run_paired_acceptance_pilot` with produced single-agent baseline decisions.

Acceptance criteria:

- Produced generator family is derived from baseline producer metadata.
- Sole decisive same-family reviewer rows create self-preference warnings.
- Unknown fixture generator family does not invent warnings.

Blocked by: Issue 1.

## Issue 3: Extend Roster Guard Without Allowing Fixture Selection

PRD promise: P6.

Public boundary for first RED test: `run_paired_acceptance_pilot`.

Acceptance criteria:

- Roster guard remains blocking on fixture-only evidence.
- Guard reasons include Cursor-default, text-only, and same-family decisive-vote risks when present.
- Required evidence includes reviewer provenance and generator-family disjointness.

Blocked by: Issues 1 and 2.
