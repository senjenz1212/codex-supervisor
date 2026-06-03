# PRD: Agentic Eval Corpus

Task id: `agentic-eval-corpus-20260603`

## Problem Statement

The agentic lead eval runner can compare execution modes, but it needs a
curated labeled corpus before operators can trust the comparison. Today the
fixture directory has a small runner-specific dataset, while real workflow
outcomes live separately in `.handoff` result files and dual-agent artifacts.
Without a schema-checked labeled set and a review-staged miner, future evals can
drift toward one-sided accept data, stale evidence references, or accidental
target-agent execution during replay.

## Solution

Add a report-only corpus layer for `agentic-lead-eval-labeled-set/v1`. The
loader validates exact schema version, task-level budgets, ordered roster
digests, transcript cassette refs, and required-verdict evidence refs. Add a
thin `.handoff` miner that produces candidate cases in a staging path only, so
human curation remains explicit. Seed the existing
`tests/fixtures/agentic_eval/` directory with a balanced 8-12 case set covering
accept, revise, deny, artifact-only, and multi-file outcomes.

## User Stories

- As an operator, I can load a curated golden set and know every evidence ref
  resolves before the eval runner scores anything.
- As a reviewer, I can inspect staged candidates from real workflow results
  before they enter the curated corpus.
- As a maintainer, I can run deterministic tests proving replay fixtures do not
  call target agents or mutate policy defaults.

## PRD Promise Contracts

P1. Schema-Versioned Labeled Set

- Public boundary: `supervisor.agentic_eval_corpus.load_agentic_eval_labeled_set`.
- Allowed outcomes: exact schema accepted; ordered tasks returned; ordered
  roster digest computed and checked.
- Forbidden outcomes: unknown schema accepted; new fixture location invented;
  runner-shaped per-arm budget accepted.

P2. Evidence and Budget Validation

- Public boundary:
  `supervisor.agentic_eval_corpus.validate_agentic_eval_labeled_set`.
- Allowed outcomes: each required verdict has `evidence_kind` in
  `probe_receipt|artifact_path|diff_hunk`; each evidence and cassette ref
  resolves; every budget uses `total_tokens` and `total_usd`.
- Forbidden outcomes: missing refs accepted; stale roster accepted; budget
  shape varies across tasks.

P3. Review-Staged Failure Miner

- Public boundary: `scripts/mine_agentic_eval_cases.py`.
- Allowed outcomes: selects non-accept final outcomes plus a balanced sample of
  clean accepts; writes a candidate set only to a staging path.
- Forbidden outcomes: auto-writing the curated corpus; nondeterministic ordering;
  target-agent or workflow execution from the miner.

P4. Balanced Seed Corpus

- Public boundary:
  `tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml`.
- Allowed outcomes: 8-12 curated cases, including clean accept, genuine revise,
  deny, artifact-only gate, and multi-file change classes.
- Forbidden outcomes: accept-only seed set; missing cassette refs; evidence refs
  that cannot be inspected on disk.

P5. Report-Only Integration Boundary

- Public boundary: focused tests plus git diff.
- Allowed outcomes: no policy default mutation; no `supervisor/state.py` change;
  no production gate sequence change.
- Forbidden outcomes: enabling fan-out, changing reviewer aggregation, or adding
  external-service execution to fixture replay.

## Implementation Decisions

- Implement a new `supervisor/agentic_eval_corpus.py` module instead of folding
  corpus validation into the existing runner. This keeps the runner slice
  report-only and avoids coupling curation to scoring.
- Keep the miner CLI as a wrapper over module functions so tests can exercise
  deterministic behavior without shell-only assertions.
- Store curated corpus, cassette refs, and evidence fixtures under the existing
  `tests/fixtures/agentic_eval/` directory.
- Represent `probe_receipt` refs as files, not bare labels, so the loader can
  perform the same concrete disk-resolution check for every evidence kind.

## Testing Decisions

- Public-boundary tests start at `load_agentic_eval_labeled_set` and
  `scripts/mine_agentic_eval_cases.py`.
- Negative tests cover bad schema version, stale roster digest, missing evidence
  refs, and per-arm budgets.
- Miner tests use a temp `.handoff` fixture with accepted, revise, and deny
  workflow-result files; output order must be stable.
- Replay guard test proves the corpus loader has no workflow runner hook and
  rejects non-fixture execution mode.

## Out Of Scope

- Running the three agentic eval arms.
- Enabling or requiring agentic fan-out.
- Changing `agentic_lead_policy`, reviewer-panel aggregation, or gate sequence.
- Changing supervisor storage, scaling, or `supervisor/state.py`.
