# Live Lead Probe Implementation Plan

## Files / Modules To Touch

- `sandbox_slug.py`
- `tests/test_slugify_label.py`
- `docs/dual-agent/live-lead-no-budget-probe-20260524-01/transcript.md`

## Risks

- Claude Code may decide to only review instead of editing during the execution gate.
- Claude Code may omit the required outcome block or report confidence without criteria.
- Pytest may fail because the temporary sandbox lacks expected import paths.

## Traceability

- P1 -> test_slugify_label_normalizes_spaces_and_symbols
- P2 -> test_slugify_label_normalizes_spaces_and_symbols
- P3 -> test_verify_workflow_claims_requires_live_receipts

## Steps

1. Run TDD review gate with live `/lead` and no edits requested.
2. Run implementation-plan gate with live `/lead` and no edits requested.
3. Run execution gate asking Claude Code to add the test, observe failure, implement, and rerun pytest.
4. Run outcome-review gate asking Claude Code to summarize the work and claims.
5. Codex collects pytest, git diff, git status, transcript, and stdout capture receipts.
