### Finding 1

Status: resolved

Concern: The adapter name could be mistaken for real official grading when no command execution occurs.

Resolution: The PRD requires explicit adapter configuration, refuses metrics without a runner, and records command receipts with return codes, output hashes, harness metadata, and artifact paths.

### Finding 2

Status: resolved

Concern: Tiny smoke runs could still require predictions for every loaded SWE-bench row if filtering happens after prediction validation.

Resolution: The implementation contract places instance-id and deterministic limit filtering before coverage enforcement and requires selection metadata in the report.

### Finding 3

Status: resolved

Concern: Oracle labels or hidden patch material could leak into public packets before decisions freeze.

Resolution: The public-boundary tests must verify exclusion of hidden fields from public packets, reviewer inputs, generator inputs, and frozen decisions before oracle receipts are written.
