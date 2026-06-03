# PRD Grill Findings: TDD Grill Findings Artifact Kind Alias

### Finding 1: Treat This As A Synonym, Not A New Role

Status: resolved

The exact failure mode is a caller vocabulary mismatch. The workflow already uses `grill_findings` for both grill documents and relies on the path to distinguish PRD grill findings from TDD grill findings. Adding `grill_findings_tdd` to the allowed set would create a second role and make required-role validation harder to reason about. The PRD resolves this by requiring the synonym to fold into the existing canonical kind.

### Finding 2: Normalize Before Typed Artifact Construction

Status: resolved

The schema rejection can happen before `_planning_artifact_role` gets a chance to use path fallback. The PRD now explicitly requires `_maybe_artifact` to feed the normalized value into `PlanningArtifact`, preserving the typed boundary while accepting the known synonym.

### Finding 3: Keep The Test At The Public Boundary

Status: resolved

A helper-only test of the regex would miss the typed-model failure. The TDD plan must assert normalization, planning-role resolution, and typed planning artifact construction for the alias forms.
