# PRD Grill Findings

### Finding 1

status: resolved

The slice could accidentally remove the legacy metadata baseline from smoke calibration and break old fixture reports. The PRD now keeps legacy calibration explicitly out of scope for removal and limits the produced-baseline requirement to powered evaluation surfaces.

### Finding 2

status: resolved

The baseline artifact could become a second candidate generator rather than a replay receipt. The PRD now requires replayable produced decisions and producer metadata only; live candidate generation remains a later slice.

### Finding 3

status: resolved

Missing baseline evidence could be reported as a baseline rejection, which would distort false reject accounting. The PRD now requires unavailable status plus a gaming flag for missing or malformed rows.

### Finding 4

status: resolved

The implementation could hide hash mismatches behind arm decision booleans. The PRD now requires candidate artifact hashes and stable baseline artifact hashes to be exported and checked against the compared candidate pool.
