# TDD Grill Findings

### Finding T1: Tests Must Hit Public Boundaries

Status: resolved

- Risk: Helper-only tests could pass while the operator path remains unwired.
- Resolution: Tests invoke `_workflow_result`, daemon `tick_once`, `run_autoresearch_fixture`, and `codex_supervisor_axi.main`.

### Finding T2: Negative Cases Are Required

Status: resolved

- Risk: The happy path could hide self-activation, cap bypass, or invalid proposal derivation.
- Resolution: Tests include below-threshold, weekly-cap, gaming/negative report, denial, and request-path guard cases.

### Finding T3: Request-Path Safety Must Be Proved By Failure Seams

Status: resolved

- Risk: A grep-only test would miss indirect execution.
- Resolution: The request-path guard monkeypatches dispatcher `run_once` and worker `Popen`; any call fails the test.

### Finding T4: Lesson Retirement Must Be Observed Through Selection

Status: resolved

- Risk: Counters might update while retired lessons are still injected.
- Resolution: The lesson feedback test asserts the retired lesson id is excluded from `query_supervisor_lessons`.

### Finding T5: Daemon Behavior Must Not Require A Live Daemon

Status: resolved

- Risk: Tests depending on launchd or long sleeps would be flaky.
- Resolution: Tick methods accept fake runners/providers and deterministic timestamps.

## Translation Audit

- Every PRD promise has at least one public-boundary test.
- Forbidden outcomes are represented as negative assertions.
- No test requires a live model, live Telegram, or launchd.
- Existing suites remain part of the regression plan.
