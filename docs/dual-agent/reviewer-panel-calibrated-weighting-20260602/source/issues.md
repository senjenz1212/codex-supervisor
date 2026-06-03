# Issues

Task id: `reviewer-panel-calibrated-weighting-20260602`

## Slice 1: Emit a versioned reviewer-panel calibration artifact

Priority: P0

Estimate: M

Scope: extend `reviewer_panel_eval_runner` with an optional calibration output
and add `build_reviewer_panel_calibration`.

Acceptance criteria:

- [ ] PRD promises: P1, P5.
- [ ] Public boundary: `reviewer_panel_eval_runner`.
- [ ] First RED test: calibration output is absent or constant-only.
- [ ] Allowed outcome: calibration includes hashes, pairwise dependency, weights,
  per-reviewer lineage, and deterministic digest.
- [ ] Forbidden outcome: weights have no source report lineage or no-lineage
  constants can become active.

## Slice 2: Apply calibrated accept aggregation

Priority: P0

Estimate: M

Scope: extend `evaluate_reviewer_panel` to accept an optional calibration
payload and compute dependency/severity-weighted aggregate accept confidence
only for all-accept panels.

Acceptance criteria:

- [ ] PRD promises: P2, P3.
- [ ] Public boundary: `evaluate_reviewer_panel` through `run_dual_agent_workflow`.
- [ ] First RED test: correlated unanimous accepts advance.
- [ ] Allowed outcome: correlated or severe accepts escalate; real important revise
  blocks.
- [ ] Forbidden outcome: weighting averages away a real objection or treats
  high-severity accepts as low-risk accepts.

## Slice 3: Thread calibration path through workflow surfaces

Priority: P1

Estimate: S

Scope: add `reviewer_panel_calibration_path` to supervisor config, CLI payload
keys, workflow params, detached submit payloads, and route metadata.

Acceptance criteria:

- [ ] PRD promises: P2, P4.
- [ ] Public boundary: `run_dual_agent_workflow`.
- [ ] First RED test: a calibration path passed through CLI payload is ignored.
- [ ] Allowed outcome: present artifact activates calibrated mode; missing artifact
  falls back to conservative mode.
- [ ] Forbidden outcome: missing calibration silently applies default weights.

## Slice 4: Preserve exports, replay, and docs

Priority: P1

Estimate: S

Scope: expose calibrated panel decisions in existing reviewer decision payloads
and add planning evidence for the measured calibration run.

Acceptance criteria:

- [ ] PRD promises: P1, P5.
- [ ] Public boundary: exported workflow artifacts and calibration eval artifacts.
- [ ] First RED test: calibration evidence is not traceable to the planning docs.
- [ ] Allowed outcome: workflow result includes calibration decision details and
  source artifacts include calibration input/report.
- [ ] Forbidden outcome: only prose claims that an eval was run.
