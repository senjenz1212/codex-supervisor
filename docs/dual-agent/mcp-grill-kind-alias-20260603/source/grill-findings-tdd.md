# TDD Grill Findings: TDD Grill Findings Artifact Kind Alias

### Finding 1: Helper Normalization Alone Is Not Enough

Status: resolved

The TDD plan now checks `_maybe_artifact` in addition to `_normalise_artifact_kind` and `_planning_artifact_role`, because the production failure can happen when Pydantic validates `PlanningArtifactKind` before downstream role resolution.

### Finding 2: Existing Behavior Needs A Negative Guard

Status: resolved

The TDD plan includes a preservation test for existing canonical kinds and an unknown kind. That keeps the fix from becoming a broad allowed-kind expansion.

### Finding 3: Durable Workflow Should Exercise The Alias

Status: resolved

The gated workflow planning artifacts should include the TDD grill artifact with kind `grill-findings-tdd`. That proves the actual supervisor pre-flight path accepts the alias instead of relying only on direct unit tests.
