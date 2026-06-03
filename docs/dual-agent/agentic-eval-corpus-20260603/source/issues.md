# Issues: Agentic Eval Corpus

## Slice 1: Labeled-Set Schema Loader

Priority: high

Scope: add `supervisor/agentic_eval_corpus.py` with exact schema validation,
ordered roster digest verification, task-level budget checks, and disk-resolved
evidence refs.

Acceptance Criteria:

- [ ] `load_agentic_eval_labeled_set` loads the seed fixture and exposes 8-12
  normalized tasks.
- [ ] Bad schema versions are rejected.
- [ ] Stale `roster_sha256` values are rejected.
- [ ] Missing evidence refs are rejected.
- [ ] Per-arm budget shapes are rejected.

PRD promises: P1, P2, P5.

## Slice 2: Review-Staged Handoff Miner

Priority: high

Scope: add `scripts/mine_agentic_eval_cases.py` and module-level miner helpers
that read `.handoff/*-workflow-result.json` files and stage candidate cases for
human review.

Acceptance Criteria:

- [ ] Fixed `.handoff` input produces deterministic candidate output.
- [ ] Non-accept final outcomes are included.
- [ ] A clean accepted sample is included.
- [ ] The script refuses to write to the curated corpus path.
- [ ] Candidate required verdicts reference captured workflow result artifacts.

PRD promises: P3, P5.

## Slice 3: Balanced Seed Corpus

Priority: high

Scope: add `tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml` and
small cassette/evidence files under the same fixture directory.

Acceptance Criteria:

- [ ] Seed fixture contains 8-12 cases.
- [ ] Seed fixture includes accept, revise, and deny outcomes.
- [ ] Seed fixture includes artifact-only and multi-file change cases.
- [ ] Every evidence and cassette ref resolves on disk.
- [ ] Replay guard proves no workflow runner or target-agent path is called.

PRD promises: P2, P4, P5.

## Coverage Index

- P1 covered by Slice 1.
- P2 covered by Slices 1 and 3.
- P3 covered by Slice 2.
- P4 covered by Slice 3.
- P5 covered by Slices 1, 2, and 3.
