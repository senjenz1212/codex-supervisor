# TDD Plan: TDD Grill Findings Artifact Kind Alias

## RED

Red: Add boundary tests that fail when the TDD grill findings synonym remains `grill_findings_tdd` instead of folding to `grill_findings`.

### test_tdd_grill_findings_kind_alias_resolves_to_grill_findings

Maps to: P1, P2; Slice 1

Before the fix, a payload with kind `grill-findings-tdd` or `grill_findings_tdd` reaches typed planning-artifact construction as the non-canonical value `grill_findings_tdd`, which is not an allowed `PlanningArtifactKind`. The test should fail by observing that the alias does not normalize to `grill_findings`, does not resolve to the `grill_findings` planning role, or cannot build a typed planning artifact.

### test_artifact_kind_normalisation_preserves_existing_kinds

Maps to: P3; Slice 2

Before the fix this test documents the baseline. It prevents the alias change from broadening unrelated artifact-kind behavior or rewriting existing canonical names.

## GREEN

Green: Implement the alias fold and typed artifact construction update until both tests pass at the MCP stdio boundary.

Implement `_normalise_artifact_kind` so any `grill*findings*tdd` spelling folds to `grill_findings`. Update `_maybe_artifact` to pass the normalized kind into `PlanningArtifact`, so the typed schema accepts the synonym after canonicalization.

## REFACTOR

Keep the implementation local to `mcp_tools/codex_supervisor_stdio.py`. Do not add a new enum literal, do not alter required planning kinds, and do not touch state or durable job tables.
