# Dual-Agent Stability Proposals

SP-N documents are review-only remediation proposals linked to observed MAST failure modes. They do not change runtime policy by themselves.

## Contract

Each proposal records:

- `proposal_id`
- `status: proposed`
- `requires_human: true`
- `cohort_id`
- `mast_code`
- evidence references, preferably replay manifests
- proposed change
- validation criteria
- non-action guarantee

## Current Runtime Source

`supervisor/stability_proposals.py` generates deterministic proposal payloads from probe cohort summaries. A proposal is considered applied only after a separate implementation slice lands tests, code, and artifacts that reference the SP id.

## Initial Mapping

| MAST code | Stability proposal theme |
|---|---|
| FM-1.2 | Covenant compliance checks before accepting reviewer decisions |
| FM-1.3 | Duplicate gate execution rejection by handoff/gate/round hash |
| FM-2.5 | Addresses-graph completeness for objections across rounds |
| FM-3.1 | Mandatory-probe completeness before gate acceptance |
| FM-3.2 | Receipt-backed verification for implementation and test claims |
| FM-3.3 | Independent cross-check evidence when verifiers disagree |
