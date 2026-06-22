# Grill Findings

### Finding 1

status: resolved

Gold-patch smoke is tempting to treat as a trustworthy positive baseline because the official oracle can pass it. The PRD and tests now require a produced baseline receipt; a gold-smoke candidate without that receipt remains unavailable and blocks matched-TAR comparison.

### Finding 2

status: resolved

A receipt object alone is not enough evidence. The implementation requires a matching candidate artifact hash, trusted decision source, candidate id, producer metadata, runner label, prompt hash, and boolean accept decision before the single-agent baseline arm can become available.

### Finding 3

status: resolved

Official live generation synthesized a baseline receipt with a decision source outside the resolver's trusted source set. The implementation now uses the existing `single_agent_candidate_generation` source while preserving the official runner label for provenance.

### Finding 4

status: resolved

Matched-TAR could be misread when the baseline is absent. The tests assert unavailable baseline arms return `baseline_arm_unavailable` instead of falling through to insufficient-pool or accept-all behavior.

### Finding 5

status: resolved

Live supervisor generation can return arbitrary structured fields, including a baseline-looking receipt. The implementation now forwards or synthesizes produced baseline receipts only when the live arm is `baseline`, and the TDD suite includes a supervisor-arm smuggling attempt.
