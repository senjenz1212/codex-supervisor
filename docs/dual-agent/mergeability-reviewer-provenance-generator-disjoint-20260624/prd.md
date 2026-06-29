## Problem Statement

The mergeability full-panel reports can now run reviewer panels, but the report does not make reviewer provider family, tool access, or generator-family disjointness explicit enough to prevent overclaiming. A Cursor reviewer with a default model can look like cross-family evidence without a resolved provider/model, a LiteLLM structured reviewer can add family diversity while remaining text-only, and a single OpenAI-family reviewer can become decisive for OpenAI-family generated patches without a visible self-preference warning.

## Solution

Add first-class reviewer provenance and generator-disjointness reporting to the mergeability paired report and bounded runner report. Per-reviewer arms and configured-panel blocks must expose provider family, lineage, tool access, assurance grade, command-evidence status, Cursor-default provenance status, LiteLLM text-only status, and same-family decisive-vote warnings. The roster-selection guard must continue to block fixture-only roster decisions and must include these provenance/disjointness requirements as explicit guard reasons.

## User Stories

1. As a benchmark operator, I want every reviewer arm to show provider family and tool access, so that I can distinguish tool-backed evidence from text-only judgment.
2. As a benchmark operator, I want Cursor default reviewers labeled as unproven cross-family evidence unless their resolved provider/model is recorded, so that I do not treat a default alias as diversity evidence.
3. As a benchmark operator, I want LiteLLM structured reviewers labeled text-only unless command evidence exists, so that family diversity is not confused with execution-grounded review.
4. As a benchmark operator, I want same-family generator/reviewer decisive votes warned in the report, so that self-preference risk is visible before any benchmark claim travels.
5. As a supervisor maintainer, I want fixture-only roster selection to remain blocked, so that easy calibration fixtures cannot justify dropping reviewers.

## PRD Promise Contracts

- P1: Per-reviewer arms expose `provider_family`, `lineage`, `tool_access`, `assurance_grade`, and `tool_backed_command_evidence`.
- P2: Configured panel reports expose an aggregate `reviewer_provenance` block with reviewer IDs and provenance warnings.
- P3: Cursor SDK reviewers whose model is absent or `default` are labeled as unproven cross-family evidence.
- P4: LiteLLM structured reviewers are labeled `text_only` unless tool-backed command evidence is present.
- P5: Reports expose `generator_disjointness` and self-preference warnings when a reviewer family matches the recorded generator family and is the sole decisive reviewer.
- P6: The roster-selection guard remains blocking on fixture-only evidence and includes provenance/disjointness evidence requirements.

## Implementation Decisions

- Use `run_paired_acceptance_pilot` and `run_bounded_parallel_panel_corpus` as the public report boundaries.
- Normalize reviewer provenance from existing independent reviewer results rather than introducing another reviewer interface.
- Add a small provider-family inference helper local to mergeability reporting for rows that predate explicit provenance fields.
- Treat `litellm_structured` as text-only by default because its runtime path is a structured chat completion without local codebase tools.
- Derive generator family from produced single-agent baseline producer metadata when available; leave it unknown when fixture metadata lacks produced-generator provenance.

## Testing Decisions

- Start with public-boundary tests that call `run_paired_acceptance_pilot` and inspect the emitted report.
- Assert per-reviewer arms include provider/tool/assurance fields.
- Assert Cursor default produces an unproven cross-family guard reason.
- Assert LiteLLM structured produces text-only provenance without command evidence.
- Assert OpenAI-family generator plus sole OpenAI-family reviewer produces a self-preference warning.
- Re-run roster-selection and public-dashboard leak tests to preserve report-only and public-surface invariants.

## Out of Scope

- Selecting or dropping reviewers.
- Making fixture-only evidence applyable.
- Adding a public mergeability rubric or abstention labels.
- Running a real benchmark.
