# Issues

## Slice 1: Add SWE-bench configured-panel preflight summaries

Priority: P0

Scope: Add report-level and per-instance configured-panel preflight blocks to SWE-bench replay outputs, summarizing whether the configured panel was invoked, which reviewers were available, which reviewers were missing, and which public transcript/output hashes exist.

PRD promise: P1, P4, P5

Public boundary for first RED test: `swebench_mergeability_replay_runner`

Chosen seam or interface: replay report emitted after public reviewer packet processing and before oracle labels are trusted

Acceptance criteria:

- [ ] Unconfigured replay reports configured_panel_invoked=false and failure_classification=uninvoked.
- [ ] Preflight summaries include available_reviewers, missing_reviewers, reviewer_results, transcript_sha256s, and output_sha256s.
- [ ] Report-only invariants remain false in replay and official replay reports.

## Slice 2: Enforce full-roster availability before S_full quality decisions

Priority: P0

Scope: Tighten configured-style panel normalization so missing reviewer verdicts are unavailable for S_full, while full-roster revise or deny decisions remain available quality rejects.

PRD promise: P2, P3, P5

Public boundary for first RED test: `swebench_mergeability_replay_runner`

Chosen seam or interface: strict panel decision normalization consumed by fixture and replay runners

Acceptance criteria:

- [ ] Missing configured reviewer verdicts keep S_full unavailable and report missing_reviewer_verdict.
- [ ] Missing configured roster evidence keeps S_full unavailable and reports missing_reviewer_roster.
- [ ] Full-roster accepting panels set full_roster_available=true and can allow S_full acceptance.
- [ ] Full-roster quality rejects keep S_full unavailable=false while S_full rejects.
- [ ] Reviewer infrastructure classifications are derived from reviewer results rather than assumed from provider names.
