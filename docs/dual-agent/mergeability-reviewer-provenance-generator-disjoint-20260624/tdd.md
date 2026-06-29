# TDD Plan

## Cycle 1: Per-Reviewer Provenance Boundary

RED: Extend the public-boundary per-reviewer-arm test to assert provider family, tool access, assurance grade, and command-evidence status are emitted by `run_paired_acceptance_pilot`.

GREEN: Normalize reviewer provenance from existing independent reviewer results and carry it into per-reviewer arm summaries.

## Cycle 2: Cursor Default Guard

RED: Add a public-boundary report test with a Cursor SDK reviewer using model `default` and assert it is not counted as proven cross-family evidence.

GREEN: Add aggregate reviewer provenance with Cursor-default status and feed it into the roster-selection guard reasons.

## Cycle 3: LiteLLM Text-Only Guard

RED: Add a public-boundary report test with a `litellm_structured` Gemini reviewer and assert the reviewer is labeled text-only without command evidence.

GREEN: Normalize LiteLLM structured reviewers as text-only unless tool-backed command evidence is explicitly present.

## Cycle 4: Generator-Reviewer Same-Family Warning

RED: Add a public-boundary report test with produced OpenAI-family baseline metadata and a sole OpenAI-family reviewer, then assert a self-preference warning is emitted.

GREEN: Derive generator family from produced baseline producer metadata and emit disjointness warnings for sole decisive same-family reviewer rows.

## Cycle 5: Guard And Dashboard Regression

RED: Re-run roster-selection and public-dashboard tests to ensure the new public projection does not leak hidden material or permit fixture-based roster selection.

GREEN: Mirror provenance/disjointness into both paired and bounded reports while preserving report-only invariants.
