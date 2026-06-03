# Reviewer Panel Calibrated Weighting PRD

Task id: `reviewer-panel-calibrated-weighting-20260602`

## Problem Statement

The reviewer panel foundation, conservative aggregator, second reviewer route,
eval runner, and adjudication are already present. The remaining gap is that
the panel still advances on the conservative rule whenever all available
reviewers accept. The eval runner can measure agreement, failure overlap, and
correlation, but its report previously declared
`does_not_emit_active_calibrated_weights`.

Operators need a calibrated accept rule that trusts reviewer agreement only to
the degree supported by measured panel eval data. Without this, two reviewers
with correlated false accepts can advance a gate with the same confidence as
two effectively independent reviewers. The fix must be conservative: measured
calibration may reduce trust in accept, but it must never soften a real revise
or deny verdict.

## Solution

This slice turns those measured eval results into a versioned calibration
artifact and lets the panel use it when the artifact exists. Calibrated accept
aggregation uses both measured dependency and severity; missing calibration must
keep the conservative behavior.

The panel eval runner emits `reviewer-panel-calibration.json` from labeled
fixtures. The workflow consumes that artifact through
`reviewer_panel_calibration_path`. When the artifact loads, all-accept panels
compute aggregate confidence from reviewer confidence, measured dependency
weight, and severity multiplier. If aggregate confidence falls below the
artifact threshold, the workflow escalates instead of advancing. Missing,
invalid, or absent calibration keeps the prior conservative aggregation path.

## User Stories

1. As a supervisor operator, I want reviewer weights to come from a measured
   eval artifact, so that accept trust is auditable and rerunnable.
2. As a gate owner, I want correlated all-accept panels to escalate, so that
   shared blind spots do not advance as if they were independent agreement.
3. As a maintainer, I want effectively independent low-severity accepts to keep
   advancing, so that calibration does not create avoidable throughput loss.
4. As a reviewer, I want important or critical revise/deny verdicts to remain
   hard blocks, so that weighting cannot average away real quality objections.
5. As an auditor, I want the workflow route and exported verdict to show
   whether calibration was active, so that replay explains why a gate advanced
   or escalated.
6. As a reliability owner, I want missing calibration to fall back to
   conservative behavior, so that configuration drift does not invent implicit
   weights.

## Blocking Open Question

Question: which reviewer dependency values should drive weights?

Resolution: run `reviewer_panel_eval_runner` on a representative labeled set
before TDD and persist the result. The calibration input, report, rows, replay
manifest, and derived calibration are attached under
`docs/dual-agent/reviewer-panel-calibrated-weighting-20260602/source/calibration-eval/`.
The calibration artifact is:

`docs/dual-agent/reviewer-panel-calibrated-weighting-20260602/source/calibration-eval/reviewer-panel-calibration.json`

The artifact records `source_report_sha256`,
`labeled_set_sha256`, `pairwise_dependency`, `reviewer_weights`, and
`calibration_sha256`. The current measured roster is Google/Gemini plus
OpenAI/Codex CLI with no overlapping failures in this labeled set, so both
weights are `1.0`. Tests add a correlated fixture proving high failure overlap
down-weights both reviewers and escalates unanimous accepts.

## PRD Promise Contracts

P1. Measured Calibration Artifact

- User-visible promise: reviewer weights are derived from a persisted panel eval
  calibration artifact, not hand-assigned constants.
- Representative action: run the panel eval with labeled tasks and cassettes,
  then inspect `reviewer-panel-calibration.json`.
- Public boundary: `reviewer_panel_eval_runner` and
  `build_reviewer_panel_calibration`.
- Allowed outcomes: deterministic weights, report hash, labeled set hash, and
  pairwise dependency signals are present; no-lineage constant blobs cannot
  activate calibrated weighting.
- Forbidden outcomes: hard-coded reviewer weights with no eval report lineage.
- Related user stories: 1, 5

P2. Calibrated Accept Aggregation

- User-visible promise: when calibration is active, all-accept panels use
  measured dependency weights and severity multipliers before advancing.
- Representative action: run `run_dual_agent_workflow` with a calibration path.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: effectively independent low-severity accepts advance;
  measured correlated accepts or high-severity accepts escalate.
- Forbidden outcomes: unanimous correlated or severe accepts auto-advance as if
  independent low-risk accepts.
- Related user stories: 2, 3, 5

P3. Hard Blocks Stay Hard

- User-visible promise: a real `revise` or `deny` at important or critical
  severity still blocks regardless of calibration.
- Representative action: one reviewer accepts and one reviewer returns
  important `revise` while calibration is active.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: panel decision remains `revise` with
  `blocking_reviewer_objection`.
- Forbidden outcomes: calibration downgrades a real reviewer objection.
- Related user stories: 4

P4. Conservative Fallback

- User-visible promise: with no calibration artifact, the panel behavior remains
  conservative and compatible with the prior slice.
- Representative action: run the workflow with no calibration path or a missing
  path.
- Public boundary: `run_dual_agent_workflow`.
- Allowed outcomes: high-confidence accepts still advance with
  `aggregation_mode=conservative`.
- Forbidden outcomes: missing calibration blocks or silently uses default
  weights.
- Related user stories: 3, 6

P5. Deterministic Replay

- User-visible promise: calibration is rerunnable from fixed eval input.
- Representative action: rerun calibration with the same eval report.
- Public boundary: `build_reviewer_panel_calibration`.
- Allowed outcomes: identical input produces identical `calibration_sha256`.
- Forbidden outcomes: timestamps, random ids, or mutable process state alter the
  calibration digest.
- Related user stories: 1, 5

## Scope

- Add a calibration builder to `supervisor/reviewer_panel_eval.py`.
- Add optional calibration consumption to `supervisor/reviewer_registry.py`.
- Thread an optional calibration path through config, workflow params, CLI
  payloads, and route metadata.
- Keep adjudication and reviewer-unavailable recovery intact.

## Non-Goals

- Do not hand-assign weights.
- Do not weaken real revise or deny blocks.
- Do not change gate order or P-probes.
- Do not touch `agentic_lead_policy` or fan-out.

## Implementation Decisions

- Calibration is opt-in through `reviewer_panel_calibration_path`; no artifact
  means the conservative aggregator remains active.
- Dependency weights are derived from pairwise same-provider, failure overlap,
  false-accept overlap, false-block overlap, and positive measured correlation
  signals from `reviewer_panel_eval_runner`.
- The active accept formula is recorded in the calibration artifact:
  `aggregate = sum(weight * confidence * severity_multiplier) / reviewer_count`.
- Real important and critical revise/deny verdicts execute before calibrated
  accept logic, preserving the previous hard-block behavior.

## Testing Decisions

- The first public-boundary tests exercise `reviewer_panel_eval_runner` and
  `run_dual_agent_workflow`, not only helper functions.
- Synthetic correlated and independent calibration fixtures prove that weights
  derive from measured data rather than constants.
- A dedicated aggregation regression asserts severity multipliers are included
  in active accept confidence.
- Full-suite verification is required because the change threads through config,
  CLI payloads, workflow routing, reviewer payload export, and replay artifacts.

## Out of Scope

- Adding new reviewer vendors or changing the reviewer roster.
- Calibrating lead fan-out or changing `agentic_lead_policy`.
- Replacing adjudication; split panels and strong objections still use the
  existing adjudication path.
- Changing P1/P2/P3/P13/P14 probes, gate order, or reviewer-unavailable
  recovery semantics.
