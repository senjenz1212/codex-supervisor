# Live Lead Probe TDD Plan

## Public Boundary

Use the sandbox repository command `python -m pytest -q` as the public boundary. The first RED should be the pytest test for the helper behavior, not a private helper-only assertion.

## Test Cases

### test_slugify_label_normalizes_spaces_and_symbols

Maps to: ISS-2, P2
RED: Add `tests/test_slugify_label.py` expecting `slugify_label(" Hello, Unity Calendar! ") == "hello-unity-calendar"` before `sandbox_slug.py` exists or before the behavior is implemented.
GREEN: Implement `sandbox_slug.slugify_label` so the test passes.

### test_verify_workflow_claims_requires_live_receipts

Maps to: ISS-2, P3
RED: Run claim verification without pytest/git receipts and expect missing receipt failures when the outcome claims tests passed and implemented.
GREEN: Supply pytest, git diff, and git status receipts collected by Codex after the live execution.

## RED/GREEN Plan

RED: Claude Code creates the pytest expectation first and observes failure.
GREEN: Claude Code implements the smallest slugify helper and reruns pytest.
Refactor: Keep the helper dependency-free and readable.
