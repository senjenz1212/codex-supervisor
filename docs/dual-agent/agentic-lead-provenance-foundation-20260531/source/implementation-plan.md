# Implementation Plan: Agentic Lead Provenance Foundation

## Slice 1: Policy Schema

- Add `agentic_lead_policy` with values `off`, `allowed`, `required`.
- Add `min_subagents`, `required_roles`, `solo_exception_for_artifact_only_gates`, and `required_evidence_grade`.
- Thread these fields through config, handoff packets, gate specs, MCP tools, and workflow CLI payloads.

## Slice 2: Evidence Grade

- Extend existing dynamic subagent receipts with runtime fields.
- Derive evidence grade in `dynamic_workflow_receipts.py`.
- Ignore declared `evidence_grade`.
- Reuse existing cwd-resolved sha256 replay verification.

## Slice 3: Required Policy Enforcement

- Block P13 when required policy lacks enough supervisor-verified subagents, required roles, transcript/output refs, hash replay, runtime metadata, or acceptable independent reviewer decisions.
- Keep P14 synthesis as the N-agent decision aggregator.

## Slice 4: Reliability Controls

- Add supervisor-owned worker spawning, stdout/stderr capture, transcript/output refs, sha256 hashes, timeout/budget/log refs, fan-out receipt ordering, and stale worker cleanup.
- Do not enable live fan-out by default.

## Slice 4b: Gate Adjudication Hardening

- Add a deterministic gate-decision probe so a typed `/lead` outcome whose own `critical_review.decision` is `revise`, `deny`, `block`, or `reject` cannot surface as an accepted supervisor gate.
- Reuse the same acceptance rule for Claude and Cursor gate convergence.

## Slice 5: Eval Report

- Add deterministic comparison report generation for `lead_direct`, `agentic_allowed`, and `agentic_required`.
- Emit report rows and summary only; do not alter defaults.
