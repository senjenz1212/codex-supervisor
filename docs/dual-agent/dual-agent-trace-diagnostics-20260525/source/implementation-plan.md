# Implementation Plan: Dual-Agent Trace Diagnostics

## Files / Modules To Touch

- `supervisor/failure_taxonomy.py`
- `supervisor/trace_envelope.py`
- `supervisor/dual_agent_runner.py`
- `supervisor/dual_agent_artifacts.py`
- `mcp_tools/codex_supervisor_stdio.py`
- `scripts/probe_live_failure_mode.py`
- `tests/test_failure_taxonomy.py`
- `tests/test_dual_agent_runner.py`
- `tests/test_dual_agent_artifacts.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `tests/test_dual_agent_live_lead_fixture.py`
- `docs/dual-agent/live-failure-mode-probe-20260525-01/*`

## Steps

1. Add MAST metadata constants and additive fields in the classifier.
2. Add deterministic reason mappings for all 14 MAST modes.
3. Add a replay sequence detection helper for cross-event MAST failures.
4. Add a timed tool-call helper using wall-clock and monotonic clocks.
5. Normalize trace-envelope tool calls so timing fields are always present.
6. Wrap owned invocation boundaries in timed records.
7. Render MAST and timing fields in Markdown artifacts.
8. Refresh the live failure-mode tri-agent probe.
9. Run full tests, compileall, diff checks, and secret scans.

## Traceability

- P1 -> `test_failure_taxonomy_maps_all_mast_modes_without_losing_supervisor_fields`
- P2 -> `test_failure_taxonomy_maps_all_mast_modes_without_losing_supervisor_fields`
- P3 -> `test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics`
- P4 -> `test_timed_tool_call_stamps_wall_clock_and_monotonic_duration`
- P5 -> `test_gate_runner_records_direct_interaction_persona_addresses_and_tool_calls`
- P6 -> `test_export_dual_agent_run_artifacts_renders_interaction_receipts`

## Risks

- Ignored-input detection must stay conservative to avoid false positives in
  normal multi-round disagreement.
- MAST labels must be additive to preserve existing audit and tests.
- Timing fields must not imply cryptographic receipts or proof of tool output.

## Validation Commands

- `uv run pytest -q`
- `python3 -m compileall -q supervisor mcp_tools scripts tests`
- `git diff --check`
- diff secret scan for API keys and bearer tokens
