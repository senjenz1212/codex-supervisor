# Grill Findings

## Finding 1: Export Alone Is Not Enforcement

Auto-exporting artifacts after a gate does not prevent an ungrounded gate from running. The MCP boundary needs a preflight that blocks before Claude Code is launched.

Resolution: `start_dual_agent_gate` and `poll_resume_signal` run artifact preflight before constructing the gate spec.

## Finding 2: Screenshots Need A First-Class Gate Signal

Screenshots added only through a later manual export can be skipped during outcome review.

Resolution: `user_facing=True` makes screenshots a strict preflight requirement for the gate call itself.

## Finding 3: Blocked Runs Still Need Artifacts

If a run blocks due to missing artifacts, the operator still needs durable evidence explaining why.

Resolution: blocked preflight writes a `dual_agent_gate_result` event with `artifact_rigor` and auto-exports Markdown projections.
