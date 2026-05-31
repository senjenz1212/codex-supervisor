# Grill Findings: TDD Gate

### Finding T1: First tests must hit user-facing control boundaries

Status: resolved

Challenge: Helper-only tests for P13 or rollout callbacks would miss the actual workflow surfaces.

Decision: The first dynamic preview failure test uses `run_dual_agent_workflow`; rollout survival uses `RolloutWatcher._drain_file` under the existing `event_ingestion_api`.

### Finding T2: Runtime tests must not depend on live Claude, Codex, Cursor, Telegram, or filesystem watches

Status: resolved

Challenge: Reliability tests that spawn live tools are flaky and would recreate the transport-failure problem.

Decision: Runtime-health tests inject fake coroutines; workflow tests use fake runners and fake Cursor; rollout tests call drain/sweep methods directly with temp files.

### Finding T3: P13 should not claim production dynamic workflow support

Status: resolved

Challenge: A validator alone does not implement parallel subagent orchestration.

Decision: This slice blocks or accepts dynamic workflow preview based on receipts. It does not fan out helpers or merge their work.

### Finding T4: New event kinds need export coverage

Status: resolved

Challenge: The traceability promise fails if `read_gate_transcript` or markdown artifacts omit P13 events.

Decision: Tests assert transcript visibility; artifact rendering gets a dedicated section for dynamic workflow receipt validation.
