# TDD Grill Findings

## Summary

The TDD plan was grilled against the PRD promises and issue slices. All findings
are resolved before implementation and supervised execution.

### Finding 1: First RED must hit runner boundary

Status: resolved

- Risk: Helper-only tests could pass while the public runner boundary remains
  absent.
- Resolution: The first new test calls
  `agentic_eval_runner(dataset_path=fixture_path)` rather than helper-only
  scoring.

### Finding 2: Evidence-required invariant needs a negative fixture

Status: resolved

- Risk: A scorer could reward verdicts that have no probe, artifact, or diff
  evidence.
- Resolution: The scorer test removes a verdict's evidence and asserts the
  verdict fails.

### Finding 3: No-live guard must prove absence of calls

Status: resolved

- Risk: A replay runner could accidentally invoke a live workflow runner.
- Resolution: The replay guard passes a sentinel `workflow_runner` that raises
  if called; fixture replay must not invoke it. Non-replay mode without
  `allow_live_calls` raises before any runner call.

### Finding 4: Replay cassettes must prove gated workflow shape

Status: resolved

- Risk: The fixture could become loose assertions rather than replay-shaped
  workflow evidence.
- Resolution: The runner validates the replay cassette contains the full
  workflow gate sequence, required P-probes, and reviewer-panel decisions before
  rows are materialized.

### Finding 5: Equal budget must be asserted before report aggregation

Status: resolved

- Risk: Rows could be emitted and only later marked suspect.
- Resolution: The runner validates all arms for a task before materializing rows
  or writing artifacts.

### Finding 6: Report-only must assert existing default-change contract

Status: resolved

- Risk: The eval could accidentally authorize policy changes.
- Resolution: Tests assert `default_change_allowed is False`, required modes are
  unchanged, and `agentic_lead_policy_snapshot.policy == "off"`.
