# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 462996 `prd_review`: P5 names report dir docs/.../agentic-eval-bridge-20260603/agentic-eval-live/ but 9 untracked dirs landed as agentic-eval-bridge-20260603-3b1eab94-* outside that path (likely run/cassette recordings; report-location promise unverified at prd gate)
- event_id 462996 `prd_review`: PRD sha256 not re-derived: shasum declined approval this gate (handoff claims 00ea6289...)
- event_id 462996 `prd_review`: agentic_eval.py + test_agentic_eval.py modified (in-scope per P4 reuse, not forbidden by Out-of-Scope; noted as modification not pure-additive)
- event_id 462997 `prd_review`: both agents accepted
- event_id 463052 `issues_review`: both agents accepted
- event_id 463242 `tdd_review`: both agents accepted
- event_id 463273 `implementation_plan`: Plan 'Files To Touch' over-declares tests/fixtures/agentic_eval/agentic_lead_bridge_dataset.yaml and bridge_cassettes/*.json which were never created (tests self-contained via tmp_path + existing curated labeled set) - cosmetic, traceability intact
- event_id 463273 `implementation_plan`: Plan 'Files To Touch' omits supervisor/agentic_eval.py though it was modified (in-scope P4 replay-shape relaxation per prior gates)
- event_id 463328 `implementation_plan`: NIT: plan Files-To-Touch over-declares tests/fixtures/agentic_eval/agentic_lead_bridge_dataset.yaml and bridge_cassettes/*.json which were never created; tests are self-contained via tmp_path + existing agentic_lead_labeled_set.yaml, so traceability is unaffected.
- event_id 463328 `implementation_plan`: NIT: plan omits supervisor/agentic_eval.py from Files-To-Touch though it was modified in-scope for P4 (replay-shape relaxation).
- event_id 463328 `implementation_plan`: Residual: shasum and pytest not independently re-run this gate (operator-approval gated); new bridge files are untracked (CI risk at later gates).
- event_id 463584 `implementation_plan`: both agents accepted
- event_id 463620 `implementation_plan`: both agents accepted
- event_id 463629 `execution`: gate blocked
- event_id 463710 `execution`: both agents accepted
- event_id 463715 `outcome_review`: required_artifacts_missing
- event_id 464264 `outcome_review`: both agents accepted
