# Evidence Status: RUNTIME-001

## Status

**Operational compatibility demonstrated for Claude Code and Codex; coding
efficacy and provenance parity remain unproven.**

## Current Repository Evidence

Focused contract tests cover the provider-neutral runtime/model seams. In
addition, the sanitized operational receipts
`compatibility-claude-code-20260713.json` and
`compatibility-codex-20260713.json` record successful no-tool executions with
the same output hash through the two live CLIs.

Focused command:

```text
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime.py tests/test_model_client.py \
  tests/test_claude_sdk_runtime.py tests/test_agent_invoker_review.py
```

This proves common result schemas with fake transports, runtime-level
resume/cancel calls, structured model-response validation, Claude SDK loading
inside its transport, and `AgentInvoker` seam usage. The operational receipts
add CLI reachability and output-schema compatibility evidence; they do not
measure coding quality.

## Open Evidence Gaps

- Provider-specific imports remain in explicit edge modules; guard tests
  prohibit them in provider-neutral core experiment modules.
- The Codex operational receipt did not expose resolved-model or cost
  provenance, so provenance parity is not established.
- The live task was deliberately a no-tool `OK` smoke, not a real coding task.
- Runtime compatibility does not prove isolated A/B/C execution, outcome
  improvement, or portability.

## Claim Boundary

The evidence supports provider-neutral contract behavior and basic live CLI
compatibility. It does not support a causal, ROI, portability, or
auto-improvement claim.
