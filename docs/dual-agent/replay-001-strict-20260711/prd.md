# PRD: REPLAY-001 Strict Replay

## Problem

Historical evidence is not trustworthy if an undeclared or unknown schema is
accepted, if an audit reads the current worktree instead of the recorded run,
or if provider aliases and execution components are not pinned in the run
manifest.

## Goal

Fail closed on incompatible replay material and perform historical evaluation
only from the run's recorded commit plus immutable workspace overlay.

## Promise Contracts

### P1: Replay schemas are explicit and compatible

- Public boundary: `check_replay_schema_versions`.
- Missing mandatory schemas and unknown versions return `incompatible`.
- Known migrations are named; they never silently reinterpret an artifact.
- Forbidden: treating an absent schema declaration as compatible.

### P2: Historical operations have distinct semantics

- Public boundary: `historical_operation_contract`.
- `rerun` executes the recorded task again, `regrade` applies a verifier to the
  frozen result, and `replay` recomputes deterministic logic from frozen input.
- Forbidden: labeling a live-checkout re-execution as replay or regrade.

### P3: Re-evaluation uses the recorded checkout

- Public boundary: `run_sampled_p11_false_accept_audit`.
- The recorded commit is materialized in a detached checkout and any captured
  dirty overlay is hash-checked before use.
- Missing, unavailable, mismatched, or unsafe checkout evidence is
  `incompatible`; the live tree is never a fallback.

### P4: The run manifest pins execution provenance

- Public boundary: `build_execution_provenance`.
- Every model lane records requested, observed, and resolved routing identity.
- Prompts, tool contracts, container/image, CLI, evaluator, and workspace
  components are content-addressed or explicitly reported missing.
- Forbidden: marking provenance complete while a required component category
  is absent.

## Non-goals

- Claiming bit-for-bit determinism for model execution.
- Reusing the current checkout when recorded material is missing.
- Treating replay evidence as causal outcome evidence.
- Running external agents or reviewers in this documentation task.

## Exit Criteria

All promise-boundary tests are green, broader quality-trend regressions are
compatible with the append-only ledger, and an evidence status document
records the exact focused command without claiming an external replay run.
