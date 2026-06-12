# TDD Grill Findings

## Findings

1. **Tests must hit the true public boundary, not helper-only calls.**
   - Resolution: finalization behavior is tested through `_workflow_result`; daemon behavior through tick seams wired into `daemon.py`; CLI behavior through `codex_supervisor_axi.main`.

2. **Request-path safety needs a negative guard.**
   - Resolution: add a regression test that monkeypatches dispatcher/spawn seams and invokes new MCP/CLI verbs. Any phase execution from request paths fails the test.

3. **Report derivation must prove skipped cases.**
   - Resolution: TDD includes accepted-positive, gaming-flagged, and non-positive metric cases.

4. **Lesson retirement must be observable through selection.**
   - Resolution: lesson feedback test asserts counters and that retired lessons are excluded from `query_supervisor_lessons`.

5. **Daemon tests should not require launchd.**
   - Resolution: use one-shot `tick_once` methods and fake runners; wiring into `daemon.py` is inspected by tests/grep-like assertions where needed.

## Translation Audit

- Every PRD promise P1-P7 is mapped to at least one test.
- First RED tests use public boundaries.
- Forbidden outcomes are represented as negative assertions.
- No test plan depends on live model, live Telegram, or live Codex Desktop.
