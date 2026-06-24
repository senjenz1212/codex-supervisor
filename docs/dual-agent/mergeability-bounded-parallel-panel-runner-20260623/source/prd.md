## Problem Statement

The mergeability measurement path can prove that the configured reviewer panel works on a tiny two-candidate diagnostic, but it cannot yet run the full fixture corpus reliably enough to produce a useful calibration report. The current public-check and full-gate measurement loop invokes each candidate and each configured reviewer serially, so a corpus-scale run spends long wall-clock time in cold reviewer calls and can be interrupted before any aggregate report is written. That leaves the team with working seams, but not the durable evidence needed to decide whether the full reviewer panel adds discrimination beyond the deterministic public floor.

## Solution

Add a bounded, checkpointed fixture corpus runner for configured full-panel mergeability measurement. The runner will keep the existing public probe and full-gate semantics, add conservative candidate and reviewer worker limits, write public-safe per-candidate checkpoint artifacts before aggregate report assembly, and resume only when hashes and roster metadata prove the checkpoint still matches the current candidate packet. It will emit an honest calibration report plus an annotation-ready HTML dashboard that humans can inspect through Lavish without changing scoring, policy, or gate authority.

## User Stories

1. As an evaluation operator, I want the full fixture corpus to run with bounded parallelism, so that a routine calibration does not require manual babysitting.
2. As an evaluation operator, I want per-candidate checkpoints before aggregation, so that interrupted reviewer work is not lost.
3. As an evaluator maintainer, I want stale checkpoints to be recomputed, so that reused evidence cannot silently drift away from the current candidate packet.
4. As a supervisor reviewer, I want timeouts and malformed reviewer verdicts to fail closed, so that unavailable evidence never becomes acceptance.
5. As a measurement consumer, I want S_probe, S_full, per-reviewer arms, inter-reviewer agreement, and discordant counts in one report, so that panel value is visible without reading raw transcripts.
6. As a safety reviewer, I want leak checks on checkpoints and HTML output, so that hidden oracle material cannot enter public review surfaces.
7. As a human annotator, I want a public-only HTML dashboard, so that I can mark questionable rows in Lavish without affecting the benchmark score.

## PRD Promise Contracts

P1. The full fixture corpus can run with bounded parallel configured reviewers and produce ordered deterministic aggregate results.
P2. Per-candidate reviewer outcomes are checkpointed before aggregation, replayable by candidate hash, and safe to resume or skip when unchanged.
P3. Timeout, missing verdict, infrastructure failure, or partial reviewer roster marks the affected candidate or arm unavailable and never accepted.
P4. The report records panel marginal status, S_probe versus S_full discordance, per-reviewer arms, inter-reviewer agreement, and report-only invariants.
P5. Annotation-ready HTML output excludes hidden oracle material and can be opened with Lavish for human review without affecting scoring.

## Implementation Decisions

The external seam is the fixture mergeability measurement interface that currently wraps `run_paired_acceptance_pilot`, because callers need one command or function that runs the corpus and receives a report. The implementation should hide worker scheduling, checkpoint validation, stale checkpoint handling, leak checking, and dashboard rendering behind that seam rather than spreading those concerns across tests or ad hoc scripts. Candidate concurrency and reviewer concurrency must be explicit knobs with conservative defaults, and the aggregate output must remain sorted by task and candidate identity even when work completes out of order.

Checkpoint identity must include candidate hash, reviewer packet hash, reviewer roster identity, relevant option values, and a schema version. A checkpoint may be reused only when all identity fields match. The configured full gate remains stricter than individual reviewer evidence: partial roster evidence can be reported for diagnosis, but S_full remains unavailable unless the configured full roster is available. Lavish is treated strictly as review UX for generated HTML; it must not be imported into metric computation, default policy selection, or benchmark scoring.

## Testing Decisions

The first tests must exercise the public fixture measurement seam with fake configured reviewers, not private helpers. They should prove bounded worker behavior, deterministic report ordering, checkpoint write-before-aggregate behavior, resume behavior for matching checkpoints, recomputation for stale checkpoints, and fail-closed unavailability for timeout or partial roster cases. Helper tests can follow only after the public-boundary tests exist, especially for leak detection and HTML rendering.

Runtime verification must include existing mergeability tests and targeted tests for the new runner. The HTML dashboard test should render from a real or representative report payload and scan for forbidden oracle keys and protected path markers. The completed real fixture run is calibration only; tests and reports must preserve `metric_applyable=false`, `improvement_claim_allowed=false`, `policy_mutated=false`, and `gate_advanced=false`.

## Out of Scope

This slice does not run SWE-bench, enable live candidate generation, change reviewer policy authority, mutate default gates, install Lavish hooks, or claim a powered improvement. It does not require a full distributed scheduler; only local bounded parallelism, checkpoint reuse, and safe resume semantics are in scope. It also does not treat a computed marginal as success by itself: zero discordance, not matched true-accept, or unavailable panel rows must be reported honestly as calibration evidence.
