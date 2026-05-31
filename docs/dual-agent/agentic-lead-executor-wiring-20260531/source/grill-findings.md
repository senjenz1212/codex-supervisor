# PRD Grill Findings

### Finding 1: Required policy must not depend on caller-authored receipts

status: resolved

question: Does the PRD distinguish the existing verifier from the missing producer?

resolution: P1 and P3 explicitly state that P13 and evidence grading already exist, and this slice only adds a supervisor-owned producer that feeds those existing checks.

### Finding 2: Lead authority must stay below Codex provenance

status: resolved

question: Could `/lead` create or certify its own runtime-native evidence?

resolution: P2 says `/lead` may only provide a roster. Codex validates, constrains, spawns, captures, hashes, and derives evidence grade. P3 forbids self-stamped grades.

### Finding 3: Parallel workers must be read-only in this slice

status: resolved

question: Does fan-out risk concurrent writes to the same worktree?

resolution: The PRD requires read-only workers, rejects writable permission modes before subprocess launch, and allows no writable parallel workers. Write-capable agentic execution is excluded from this implementation slice.

### Finding 4: Solo exception scope needs a gate identity

status: resolved

question: Can the current boolean excuse solo execution outside artifact-only gates?

resolution: P4 requires passing gate identity into policy evaluation and accepting the exception only for artifact-only gate names.

### Finding 5: Transport scope remains bounded

status: resolved

question: Does the PRD overclaim raw MCP transport recovery?

resolution: P5 covers payload preservation through the existing detached workflow path. Raw MCP auto-reconnect remains explicitly out of scope.
