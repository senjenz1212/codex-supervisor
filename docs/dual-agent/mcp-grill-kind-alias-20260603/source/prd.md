# PRD: TDD Grill Findings Artifact Kind Alias

## Problem Statement

The durable supervisor workflow accepts planning artifacts through a typed planning-artifact schema before the gate can validate the files themselves. The workflow driver already models the TDD grill findings as the canonical `grill_findings` role and differentiates the PRD grill from the TDD grill by path, but external callers can reasonably submit the more specific artifact kind `grill-findings-tdd`. Today that spelling normalizes to `grill_findings_tdd`, which is not accepted by the typed planning-artifact model or the planning-role set. A caller can therefore be rejected before the gate even reaches the actual artifact validation, even though the path and workflow convention are valid.

This is a vocabulary compatibility gap, not a gate-quality decision, transport issue, or new artifact role. The fix should make the known spelling land on the existing `grill_findings` role and leave the allowed kind set unchanged.

## Solution

Fold the known TDD grill findings synonym at the artifact-kind normalization boundary. Any caller spelling matching `grill*findings*tdd`, including `grill-findings-tdd` and `grill_findings_tdd`, becomes `grill_findings`. The normalized value is then used when constructing the typed planning artifact so schema validation sees the canonical role rather than the caller synonym.

The change remains intentionally narrow: no new planning kind, no gate-sequence changes, no state migration, and no relaxed acceptance semantics. The path `source/grill-findings-tdd.md` remains the way the two grill findings documents are differentiated.

## User Stories

- As a workflow caller, I can submit the TDD grill findings artifact using the specific kind `grill-findings-tdd` without being rejected before the gate starts.
- As a supervisor maintainer, I can keep one canonical planning role, `grill_findings`, while still accepting the workflow driver's TDD grill artifact spelling.
- As a gate reviewer, I still see the same required planning roles and gate semantics; the alias does not create a second grill role or weaken validation.

## PRD Promise Contracts

P1. Caller-supplied `grill-findings-tdd` maps to canonical planning role `grill_findings`.

Promise: Caller-supplied `grill-findings-tdd` maps to canonical planning role `grill_findings`.
Public boundary: `mcp_tools.codex_supervisor_stdio` artifact normalization and planning-artifact conversion.
Representative action: submit a planning artifact with kind `grill-findings-tdd` and path `source/grill-findings-tdd.md`.
Allowed outcomes: normalized kind is `grill_findings`; planning role resolves as `grill_findings`; typed planning artifact construction succeeds.
Forbidden outcomes: pre-flight schema rejection; adding a new allowed kind; treating the artifact as `other`.

P2. Caller-supplied `grill_findings_tdd` and equivalent spellings map to canonical planning role.

Promise: Caller-supplied `grill_findings_tdd` and equivalent `grill*findings*tdd` spellings map to the same canonical role.
Public boundary: `_normalise_artifact_kind` and `_planning_artifact_role`.
Representative action: normalize underscore, hyphen, or dotted spellings.
Allowed outcomes: each spelling returns `grill_findings` and can be used as a planning artifact kind.
Forbidden outcomes: accepting only one spelling while rejecting the other known spelling; path-only fallback masking a kind bug.

P3. Existing artifact kinds keep their current behavior.

Promise: Existing artifact kinds keep their current behavior.
Public boundary: artifact kind normalization and typed planning artifact construction.
Representative action: normalize `prd`, `tdd_plan`, `grill_findings`, and `implementation_plan`.
Allowed outcomes: canonical existing kinds remain unchanged; unknown kinds still stay unknown for downstream rejection.
Forbidden outcomes: expanding the allowed-kind set, changing gate logic, or silently converting unrelated kinds.

## Implementation Decisions

- Implement the fold in `_normalise_artifact_kind` so all downstream callers share one spelling rule.
- Use the normalized artifact kind when building `PlanningArtifact` in `_maybe_artifact`; otherwise Pydantic rejects the synonym before planning-role resolution can occur.
- Keep `PlanningArtifactKind` and the artifact-rigor allowed role set unchanged.

## Testing Decisions

- Add focused tests at the MCP stdio boundary because the failure happens before the workflow gate can make a verdict.
- Verify both `grill-findings-tdd` and `grill_findings_tdd` normalize to `grill_findings` and resolve as the `grill_findings` planning role.
- Verify existing canonical artifact kinds still normalize unchanged.

## Out of Scope

- No new planning artifact kind.
- No changes to workflow gate sequencing, P-probes, reviewer semantics, or durable submit/poll behavior.
- No changes to `supervisor/state.py`.
