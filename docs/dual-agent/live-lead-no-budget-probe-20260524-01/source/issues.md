# Live Lead Probe Issues

## Slice ISS-1: Review Tiny TDD Plan

Type: Live probe
Priority: P0
Estimate: S
Scope: Confirm the PRD/TDD artifacts are strong enough for Claude Code `/lead` to understand the sandbox task.
PRD promise: P1, P2
First public-boundary RED test: `test_slugify_label_normalizes_spaces_and_symbols`.

Acceptance Criteria:
- [ ] TDD review gate accepts the public-boundary test name.
- [ ] The test names the sandbox behavior in product terms.
- [ ] The plan avoids user-facing or external-provider work.

## Slice ISS-2: Execute Sandbox Implementation

Type: Live probe
Priority: P0
Estimate: S
Scope: Add a failing pytest test and implement `slugify_label` in the disposable sandbox repository.
PRD promise: P2, P3
First public-boundary RED test: `python -m pytest -q` inside the sandbox.

Acceptance Criteria:
- [ ] Test file is added under `tests/test_slugify_label.py`.
- [ ] Implementation file is added under `sandbox_slug.py`.
- [ ] Final pytest receipt passes.
- [ ] Git status shows the changed files for receipt verification.
