# TDD Grill Findings

## Status

Accepted after tightening the first RED test around the public runner boundary.

## Findings

1. A helper-only baseline receipt test would miss the user-visible failure mode. Resolution: the first RED test calls the measurement runner and reads the exported report.
2. A panel-available assertion alone would not prove reviewer evidence survived export. Resolution: the second RED test checks reviewer ids, packet hashes, decisions, and rationale evidence on per-candidate rows.
3. Treating unavailable reviewers as a reported metric would repeat the previous wiring-only pattern. Resolution: the runner raises `MergeabilityBenchError` when the configured full gate is unavailable.
4. The primary comparison might be added without demoting the legacy headline. Resolution: the first RED test checks `comparisons.supervisor_vs_single_agent_baseline.primary=true` and metadata comparison `primary=false`.
5. The tests must not require live Cursor or Codex in unit runs. Resolution: tests inject reviewer adapters through `ConfiguredReviewerPanelOptions`, while the production runner defaults to the real configured roster.

## Waivers

No waivers. Live reviewer execution for the final artifact may be unavailable in the local environment; if that happens, the measurement is blocked rather than relabeled as accepted.
