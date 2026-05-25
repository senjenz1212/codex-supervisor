# TDD Plan: Dual-Agent Trace Diagnostics

## Public Boundary RED 1 - MAST Classifier

Boundary: `dual_agent_slice0`

Test file: `tests/test_failure_taxonomy.py`

First RED:

`test_failure_taxonomy_maps_all_mast_modes_without_losing_supervisor_fields`

Behavior:

- exercise `classify_failure`
- assert all 14 MAST codes are reachable
- assert existing fields are still present
- assert internal categories can return `mast_code is None`

## Public Boundary RED 2 - Trace Envelope Timing

Boundary: `dual_agent_slice0`

Test file: `tests/test_failure_taxonomy.py`

First RED:

`test_timed_tool_call_stamps_wall_clock_and_monotonic_duration`

Behavior:

- use fake wall and monotonic clocks
- assert `started_at_ms`, `ended_at_ms`, and `duration_ms`
- assert status and existing metadata survive

## Public Boundary RED 3 - Runner Tool Timing

Boundary: `dual_agent_runner`

Test file: `tests/test_dual_agent_runner.py`

First RED:

`test_gate_runner_records_direct_interaction_persona_addresses_and_tool_calls`

Behavior:

- extend existing test to assert every direct gate tool call has timing fields
- assert `invoke_claude_lead` has a measured duration
- assert planning, handoff, P1, P2, and P3 logical calls have timing fields

## Public Boundary RED 4 - Replay Sequence Failures

Boundary: artifact export

Test file: `tests/test_dual_agent_artifacts.py`

First RED:

`test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics`

Behavior:

- create fixture events for repeated gate input
- create fixture events for ignored objection
- create fixture events for accepted result missing required probes
- create fixture events for Cursor rejection after acceptance
- assert `replay/manifest.json` includes MAST-coded sequence diagnostics

## Public Boundary RED 5 - Artifact Projection

Boundary: artifact export

Test file: `tests/test_dual_agent_artifacts.py`

First RED:

`test_export_dual_agent_run_artifacts_renders_mast_and_tool_timing`

Behavior:

- create an event with a MAST-coded taxonomy and timed tool call
- assert `interactions.md` and `transcript.md` show MAST code/mode and timing

## Live Evidence

After unit tests pass:

- rerun `scripts/probe_live_failure_mode.py`
- preserve refreshed `summary.json`, `interactions.md`, `transcript.md`,
  `transcript.jsonl`, `replay/manifest.json`, and fixtures
- confirm final status remains `blocked_as_expected`
- confirm Claude and Cursor tool calls include timing
