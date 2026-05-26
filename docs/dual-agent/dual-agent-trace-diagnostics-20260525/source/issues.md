# Issues: Dual-Agent Trace Diagnostics

## Issue 1 - Add MAST Mode Fields

PRD promise: P1, P2

Public boundary: `dual_agent_slice0`

Representative action: call `classify_failure` and `stamp_trace_envelope` with
fixture failure payloads.

Allowed outcomes:

- existing `category`, `subcategory`, and `code` remain
- MAST fields are present on every taxonomy payload
- internal supervisor-only failures use `mast_code: null`

Forbidden outcomes:

- external LLM classification
- breaking existing category assertions
- only a subset of MAST modes reachable by tests

TDD plan:

- RED: add tests that require all 14 MAST modes to be reachable.
- GREEN: add deterministic reason mapping and additive fields.

## Issue 2 - Detect Cross-Event MAST Failures

PRD promise: P3

Public boundary: artifact export

Representative action: export a synthetic run containing sequence failure
patterns.

Allowed outcomes:

- replay manifest contains deterministic sequence failure entries
- entries include event ids and MAST code/mode
- `mast-coverage.md` and `replay/mast-coverage.json` list every MAST mode with
  deterministic trigger surface, entrypoint, and observed run evidence

Forbidden outcomes:

- sequence failures are only visible by manually reading Markdown
- noisy ignored-input detection fires without an objection or without a
  following response

TDD plan:

- RED: artifact export tests for repeated gate input, ignored objection,
  accepted result missing required probes, and Cursor disagreement.
- GREEN: add replay sequence detection helper and wire it into the manifest.

## Issue 3 - Add Per-Tool-Call Timing

PRD promise: P4, P5

Public boundary: `dual_agent_runner`

Representative action: run a fixture-backed direct gate and inspect
`trace_envelope.tool_calls`.

Allowed outcomes:

- every tool call has `started_at_ms`, `ended_at_ms`, and `duration_ms`
- Claude and Cursor invocation boundaries have measured duration
- existing tool call fields remain intact

Forbidden outcomes:

- timing only appears in new code paths
- timing fields appear in raw metadata but not the trace envelope

TDD plan:

- RED: tests assert complete timing fields on stamped and runner-produced tool
  calls.
- GREEN: add timing helper and wire major invocation sites.

## Issue 4 - Project New Diagnostics Into Artifacts

PRD promise: P6

Public boundary: artifact export

Representative action: export docs for a run with MAST failure and timed tool
calls.

Allowed outcomes:

- Markdown shows MAST code/mode and timing
- triage shows aggregate run totals, missing-receipt links, probe ids, and
  failure reasons in the slowest-tool table
- manifest includes sequence failures and per-mode MAST coverage pointers
- live failure-mode probe artifacts are refreshed when credentials are present,
  or the refresh is explicitly reported as credential-gated

Forbidden outcomes:

- `transcript.jsonl` has fields but Markdown omits them
- manifest omits the run-level diagnostic summary

TDD plan:

- RED: artifact tests require MAST and timing strings.
- GREEN: update renderer and replay manifest.
