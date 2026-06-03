# Issues: TDD Grill Findings Artifact Kind Alias

## Slice 1: Normalize TDD Grill Kind Synonyms

Priority: high

Scope: Fold caller-supplied TDD grill findings spellings into the canonical `grill_findings` kind at the MCP stdio artifact normalization boundary. Use the normalized kind when constructing typed planning artifacts.

Acceptance criteria:

- [ ] `grill-findings-tdd` normalizes to `grill_findings`.
- [ ] `grill_findings_tdd` normalizes to `grill_findings`.
- [ ] A typed `PlanningArtifact` can be created from those spellings without schema rejection.
- [ ] No new planning artifact kind is added.

PRD promise: P1, P2

## Slice 2: Preserve Existing Kind Behavior

Priority: high

Scope: Add regression coverage so existing canonical artifact kinds continue to normalize unchanged and unrelated unknown kinds are not silently converted.

Acceptance criteria:

- [ ] `prd`, `tdd_plan`, `grill_findings`, and `implementation_plan` remain unchanged.
- [ ] Unknown artifact kinds remain unknown for downstream validation.
- [ ] No gate semantics, P-probes, durable job handling, or `supervisor/state.py` behavior changes.

PRD promise: P3
