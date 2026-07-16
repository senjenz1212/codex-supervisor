# TDD Grill Findings

## Finding 1: Flattened synthetic fixtures would repeat the defect

status: resolved

The new JSONL fixtures preserve the real nested Codex and Claude structures.
Legacy flattened watcher tests remain only as compatibility regressions.

## Finding 2: A helper-only kind test would not prove terminal state

status: resolved

The captured fixture tests call `_drain_file`, then assert durable event kinds,
run status, and the queued `evaluate_run` decision.

## Finding 3: A registry-file test alone would not prove workflow joins

status: resolved

The submission test drains a captured rollout and joins `events.run_id` to
`dual_agent_workflow_jobs.run_id`, asserting one task/run/workflow.

## Finding 4: Low-similarity coverage could still hide the L1 prerequisite

status: resolved

The semantic test writes an allowed-path change and asserts the L1 verdict has
zero findings before checking L2, L3, and L4 evidence.

## Finding 5: Model-backed tests would miss deterministic signals

status: resolved

Separate tests run with both clients absent and prove repetition, repeated tool
errors, and no-progress age can independently enqueue adjudication.
