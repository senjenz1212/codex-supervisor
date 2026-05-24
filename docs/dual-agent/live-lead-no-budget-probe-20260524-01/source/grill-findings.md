# Live Lead Probe Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could this probe mutate the real codex-supervisor repo?
resolution: The execution cwd is a disposable temporary git repository, not the main repo.

### Finding G2

status: resolved
severity: high
question: Could the probe be mistaken for fixture replay?
resolution: The runner wraps real `subprocess.run` and captures every live Claude stdout/stderr file; no replay runner is used.

### Finding G3

status: waived
severity: medium
question: Should Cursor participate in this first live probe?
reason: Probe 1 intentionally excludes Cursor so Claude `/lead` and Codex/supervisor receipt handling are isolated.

## Decision

No open findings remain.
