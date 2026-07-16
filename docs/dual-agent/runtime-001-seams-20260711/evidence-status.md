# Evidence Status: RUNTIME-001

## Status

**Basic CLI compatibility is demonstrated for Claude Code and Codex.
Operational A/B/C execution remains blocked.**

## Current Repository Evidence

Focused contract tests cover the provider-neutral runtime/model seams. In
addition, the sanitized compatibility receipts
`compatibility-claude-code-20260713.json` and
`compatibility-codex-20260713.json` record successful no-tool executions with
the same output hash through the two live CLIs.

The executable manifest now records the path selected for invocation separately
from the final symlink target and hashes the final readable executable. Local
checks on July 13, 2026 resolved the installed `claude` and `codex` symlinks and
produced complete binary manifests.

The concrete `ClaudeCodeRuntime` and `CodexRuntime` classes are also exercised
through the real local subprocess transport with controlled executable
fixtures. Those tests intentionally do **not** pass the operational
`RepositoryArmExecutor` gate: the local subprocess backend records what it
actually enforced and names the remaining pins instead of copying requested
values into an attestation.

Focused command:

```text
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime.py tests/test_arm_executor.py \
  tests/test_model_client.py \
  tests/test_claude_sdk_runtime.py tests/test_agent_invoker_review.py
```

Result on July 13, 2026: **103 passed, 1 skipped**. A broader run adding
`tests/test_experiment_kernel.py` produced **194 passed, 1 skipped**.

This proves common result schemas with fake transports, runtime-level
resume/cancel calls, structured model-response validation, Claude SDK loading
inside its transport, and `AgentInvoker` seam usage. The compatibility receipts
add CLI reachability and output-schema compatibility evidence; they do not
measure coding quality or establish an operational experiment backend.

## Open Evidence Gaps

- Provider-specific imports remain in explicit edge modules; guard tests
  prohibit them in provider-neutral core experiment modules.
- The Codex compatibility receipt did not expose resolved-model or cost
  provenance. The runtime now reports these as explicit provenance blockers;
  it does not infer the served model from the requested model or invent cost.
- The local subprocess backend does not instantiate the TaskSpec container
  image and does not hard-enforce token or cost ceilings. Its operational
  attestation therefore carries `enforced: false` and names these unmet pins.
- The macOS subprocess path can report its actual workspace sandbox, network
  rule, process containment, host platform, and timeout enforcement. These
  facts are insufficient to satisfy the full frozen environment contract.
- The live task was deliberately a no-tool `OK` smoke, not a real coding task.
- Runtime compatibility does not prove isolated A/B/C execution, outcome
  improvement, or portability.

## Claim Boundary

The evidence supports provider-neutral contract behavior, symlink-aware
executable identity, explicit backend/provenance blockers, and basic live CLI
compatibility. The pilot remains blocked until a backend genuinely enforces the
pinned image and all required resource/network controls, and each provider
emits exact experiment-grade model, token, and cost provenance. This evidence
does not support a causal, ROI, portability, or auto-improvement claim.
