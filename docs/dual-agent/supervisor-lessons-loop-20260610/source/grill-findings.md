# PRD Grill Findings

### Finding 1 - Lesson Authority Must Stay Below Gates

Status: resolved

Risk: a known-failure note could be mistaken for proof that a future gate handled the issue. Resolution: the PRD states that lessons are advisory metadata only and cannot satisfy probes, receipts, reviewer decisions, or typed outcomes.

### Finding 2 - Matching Needs A Stable Task Class

Status: resolved

Risk: many workflows do not pass an explicit dynamic task class. Resolution: the design uses `dynamic_workflow_task_class` when available and otherwise falls back to routed workflow complexity, recording the chosen `lesson_task_class` in the route snapshot.

### Finding 3 - Hash Must Cover Canonical Text

Status: resolved

Risk: storing only lesson ids would make replay depend on a formatter that could drift. Resolution: the injected block is built by one canonical helper and the exact text plus SHA-256 hash are written to ledger and handoff surfaces.

### Finding 4 - Run-End Hook Must Be Idempotent

Status: resolved

Risk: detached durable jobs can be polled or caught up more than once. Resolution: lesson ids are deterministic over task class, gate, taxonomy, root cause, remediation, and source run, so repeated extraction returns the same row.

### Finding 5 - Source Evidence Must Be Deterministic

Status: resolved

Risk: generating lessons from free-form model prose would become a hallucination vector. Resolution: extraction is limited to trace-envelope taxonomy, reviewer disagreement payloads, sequence failures, and explicit drift/probe status.
