# Issues

## OBS-1: Normalize captured target events

Scope:
- Descend through `payload.type`.
- Normalize Claude transcript, Codex rollout/CLI, and OpenCode event shapes.
- Retain raw payloads and callback compatibility.
- End runs and enqueue evaluation from canonical terminal kinds.

Acceptance:
- Captured nested Codex and Claude rollouts finish `completed`.
- Canonical event sequences are asserted.
- OpenCode session/message/tool forms map to the same taxonomy.

## OBS-2: Register submission and join rollout provenance

Scope:
- Add a run-registry integration module.
- Resolve target-session identity at submission.
- Co-register through `State.register_run`.
- Atomically write a session sidecar.
- Enrich ingested rollout payloads with workflow, task, and target-session IDs.

Acceptance:
- Submission is immediately visible in `active_runs()`.
- Every captured rollout row uses the submitted workflow run ID.
- The event-to-job join produces one task, one run, and one workflow.
- Same-token reattach cannot change the persisted session join.

## OBS-3: Decouple semantic adjudication

Scope:
- Remove the early return below the L1 threshold.
- Use normalized agent messages when explicit intent summaries are absent.
- Let scope, similarity, plan progress, repetition, tool errors, and stalls
  independently open L4.

Acceptance:
- Goal abandonment in allowed files writes L1/L2/L3 verdicts and enqueues L4.
- Evidence reports zero path violations and semantic signal names.
- Deterministic repetition/tool-error/stall signals work without model clients.
