# Implementation Plan: TDD Grill Findings Artifact Kind Alias

## Files / Modules To Touch

- `mcp_tools/codex_supervisor_stdio.py`
- `tests/test_codex_supervisor_mcp_stdio.py`
- `docs/dual-agent/mcp-grill-kind-alias-20260603/source/prd.md`
- `docs/dual-agent/mcp-grill-kind-alias-20260603/source/issues.md`
- `docs/dual-agent/mcp-grill-kind-alias-20260603/source/tdd.md`
- `docs/dual-agent/mcp-grill-kind-alias-20260603/source/grill-findings.md`
- `docs/dual-agent/mcp-grill-kind-alias-20260603/source/grill-findings-tdd.md`

## Steps

1. Add a synonym fold in `_normalise_artifact_kind` for `grill*findings*tdd` spellings.
2. Use `_normalise_artifact_kind` in `_maybe_artifact` before constructing `PlanningArtifact`.
3. Add focused tests for alias normalization, planning-role resolution, typed artifact construction, and existing-kind preservation.
4. Run focused tests, related MCP stdio tests, and the full suite before commit.

## Risks

- The main risk is accidentally adding a new artifact role instead of folding into `grill_findings`; the implementation avoids changing the allowed-kind set.
- Another risk is a path-only fallback hiding schema rejection; the test explicitly exercises typed planning artifact construction.
- A broad regex could rewrite unrelated kinds; the regex anchors on `grill` before `findings` before `tdd`, and an existing-kind test protects canonical spellings.

## Traceability

- P1 is covered by `test_tdd_grill_findings_kind_alias_resolves_to_grill_findings`.
- P2 is covered by `test_tdd_grill_findings_kind_alias_resolves_to_grill_findings`.
- P3 is covered by `test_artifact_kind_normalisation_preserves_existing_kinds`.
