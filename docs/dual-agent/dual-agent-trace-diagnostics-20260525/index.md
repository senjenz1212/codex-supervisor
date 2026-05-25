# Dual-Agent Trace Diagnostics Slice

- task_id: `dual-agent-trace-diagnostics-20260525`
- status: `accepted`
- owner: Codex supervisor lifecycle reviewer
- live evidence: `docs/dual-agent/live-failure-mode-probe-20260525-01/`

## Source Artifacts

- `source/prd.md`
- `source/grill-findings.md`
- `source/issues.md`
- `source/tdd.md`
- `source/grill-findings-tdd.md`
- `source/implementation-plan.md`

## Implementation Summary

- Added deterministic MAST metadata for all 14 modes while preserving existing
  supervisor `category`, `subcategory`, and `code` fields.
- Added replay sequence diagnostics for duplicate gate input, ignored objection,
  accepted result missing required probes, and Cursor disagreement after an
  accepted gate.
- Added supervisor-measured timing fields for trace-envelope tool calls:
  `started_at_ms`, `ended_at_ms`, and `duration_ms`.
- Split Cursor SDK-reported duration into `cursor_duration_ms` on tool-call
  records so it does not overwrite supervisor wall-clock timing.
- Projected MAST and timing fields into Markdown artifacts and
  `replay/manifest.json`.

## Validation

- `uv run pytest -q` -> 418 passed
- `python3 -m compileall -q supervisor mcp_tools scripts tests` -> passed
- `git diff --check` -> passed
- diff secret scan for Cursor/OpenAI/GitHub/Bearer token patterns -> clean
- live tri-agent failure-mode probe -> `blocked_as_expected`

## Live Tri-Agent Result

- Claude Code `/lead`: accepted the deliberate phantom fixture.
- Cursor SDK (`composer-2.5`): accepted the fixture as coherent and read-only.
- Codex/supervisor: blocked final completion with `P11` /
  `workflow_claim_verification_failed`.
- MAST code: `FM-3.2` / `No or incomplete verification`.
- Timing proof: `transcript.jsonl` contains complete timing fields on every
  trace-envelope tool call.
