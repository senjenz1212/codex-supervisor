# Grill Findings

### Finding 1

status: resolved

Configured panel results can be available in aggregate while still missing one reviewer verdict. The implementation now treats missing reviewers as full-roster unavailable for S_full rather than accepting a partial panel as a quality decision.

### Finding 1A

status: resolved

Configured panel results can also omit roster evidence entirely while claiming an available accepting reviewer. The implementation now requires explicit reviewer_ids before full_roster_available can become true.

### Finding 2

status: resolved

Quality rejection must not be collapsed into infrastructure failure. The PRD and tests require full-roster revise or deny results to remain available quality decisions with S_full reject and unavailable false.

### Finding 3

status: resolved

Preflight evidence could accidentally expose hidden oracle details. The slice records only public reviewer result summaries, reviewer ids, failure classifications, transcript hashes, and output hashes, while existing reviewer packet leak checks remain in force.
