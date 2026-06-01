# Reviewer Contract Reliability Implementation Plan

## Goal

Implement the Phase 0-selected structured LiteLLM reviewer route while keeping
the existing Cursor SDK route available and preserving gate semantics.

## Planned Changes

1. Add reviewer config defaults:
   - `reviewer_model = gemini-3.1-pro-preview`
   - `reviewer_output_mode = litellm_structured`
   - keep `cursor_sdk` as an explicit compatibility mode.

2. Thread reviewer fields through:
   - config
   - `run_dual_agent_workflow`
   - submit-job payloads
   - CLI workflow payload filtering
   - `CursorInvocationRequest`

3. Add structured reviewer execution:
   - call the configured OpenAI-compatible LiteLLM gateway
   - request strict JSON schema output
   - enforce lowercase decision enums
   - wrap returned JSON in the typed outcome block for the existing fidelity
     validator
   - reuse critical-review completeness checks

4. Preserve safety semantics:
   - read-only git status guard remains
   - valid `revise` and `deny` outcomes still block
   - gateway failures classify as reviewer infrastructure unavailable
   - invalid/missing structured output classifies as reviewer contract unmet
   - degraded recovery remains separate and explicit

5. Add deterministic tests:
   - config/CLI round-trip
   - structured success
   - structured contract miss
   - structured infrastructure failure
   - valid rejection blocks
   - route metadata is exported

## Files / Modules To Touch

- `supervisor/config.py`
  - Add reviewer model/output-mode config defaults and validation.
- `supervisor/cursor_agent.py`
  - Keep the existing Cursor SDK path and add the structured LiteLLM reviewer
    path behind the same independent-review boundary.
- `mcp_tools/codex_supervisor_stdio.py`
  - Thread reviewer fields through `run_dual_agent_workflow`, job payloads,
    reviewer invocation, ledger events, and recovery metadata.
- `mcp_tools/codex_supervisor_workflow_cli.py`
  - Preserve reviewer fields in CLI request parsing and fallback execution.
- `config.example.yaml`
  - Document the new reviewer defaults without requiring live credentials.
- `tests/test_cursor_agent.py`
  - Cover structured reviewer success, compatibility routing, read-only guard,
    contract failures, truncation, infrastructure failures, and metadata.
- `tests/test_dual_agent_workflow_driver.py`
  - Cover valid reviewer `revise`/`deny` blocking and downstream invocation
    ordering when Claude has already accepted.
- `tests/test_codex_supervisor_mcp_stdio.py`
  - Cover MCP/config field threading and default behavior where needed.

## Non-Goals

No reviewer registry, no multi-reviewer fan-out, no agentic executor wiring, no
P13/P14 changes, and no weakening of the typed outcome contract.

## Risks

- Risk: structured LiteLLM output bypasses the existing typed outcome contract.
  Mitigation: wrap schema output in `<dual_agent_outcome>` and reuse
  `evaluate_outcome_fidelity` plus critical-review completeness checks.
- Risk: lowercase enum requirements drift from the runtime schema.
  Mitigation: test valid lowercase decisions and classify casing/schema
  mismatches as `reviewer_contract_unmet`, never as accept.
- Risk: default reviewer changes overload or break the legacy `cursor_model`
  field.
  Mitigation: keep `cursor_sdk` as an explicit output mode and preserve
  compatibility tests for the existing Cursor SDK runner.
- Risk: the structured path can mutate the worktree because it does not use the
  existing reviewer read-only guard.
  Mitigation: wrap every reviewer route with the same before/after
  `_git_status` check and block as `cursor_modified_worktree` on drift.
- Risk: valid `revise` or `deny` verdicts are misclassified as unavailable
  infrastructure to make the gate proceed.
  Mitigation: keep valid reviewer outcomes in the existing AND algebra and add
  workflow regression tests for both `revise` and `deny`.
- Risk: gateway failures and truncated JSON share a failure domain with the
  workflow runner.
  Mitigation: classify gateway exceptions as
  `reviewer_infrastructure_unavailable`, classify invalid/truncated output as
  `reviewer_contract_unmet`, and keep `reviewer_unavailable_policy` recovery
  separate from real verdict handling.
- Risk: defaulting to Gemini via LiteLLM makes deterministic tests depend on
  network credentials.
  Mitigation: mock below the public reviewer boundary and keep live Phase 0
  evidence only as traceability artifacts.

## Traceability

- P1 Reviewer route is configurable
  - Planned changes: config defaults, MCP/workflow threading, CLI key
    preservation, Cursor SDK compatibility mode.
  - TDD refs:
    `test_select_reviewer_defaults_to_phase0_structured_gemini`,
    `test_workflow_kwargs_from_payload_preserves_reviewer_fields`,
    `test_cursor_sdk_output_mode_routes_to_cursor_sdk_runner`.
- P2 Structured reviewer emits the same typed outcome contract
  - Planned changes: strict JSON schema, typed block wrapping,
    `evaluate_outcome_fidelity`, critical-review completeness, read-only guard.
  - TDD refs:
    `test_structured_litellm_reviewer_returns_fidelity_passing_outcome`,
    `test_structured_litellm_reviewer_preserves_read_only_guard`,
    `test_structured_litellm_reviewer_enforces_contract_completeness`.
- P3 Cross-lineage independence is preserved
  - Planned changes: non-Claude Gemini LiteLLM default, downstream reviewer
    invocation after Claude acceptance, route metadata.
  - TDD refs:
    `test_reviewer_route_metadata_is_recorded`,
    `test_workflow_invokes_reviewer_after_claude_accept_with_non_claude_default`.
- P4 Real reviewer rejection still blocks
  - Planned changes: preserve existing AND-verdict blocking for valid reviewer
    `revise` and `deny`.
  - TDD refs:
    `test_structured_litellm_reviewer_revise_blocks_workflow`,
    `test_structured_litellm_reviewer_deny_blocks_workflow`.
- P5 Genuine infrastructure failure keeps recovery behavior
  - Planned changes: explicit infrastructure/contract-unmet classification
    feeding the existing reviewer-unavailable recovery policy.
  - TDD refs:
    `test_structured_litellm_failure_classifies_as_infrastructure_unavailable`,
    `test_structured_litellm_invalid_json_classifies_as_contract_unmet`,
    `test_structured_litellm_truncation_classifies_as_contract_unmet`.
- P6 Replay and evidence remain deterministic
  - Planned changes: mocked deterministic tests, route metadata receipts, Phase
    0 evidence retained as artifacts.
  - TDD refs:
    `test_reviewer_route_metadata_is_recorded`,
    `test_structured_litellm_reviewer_returns_fidelity_passing_outcome`.
