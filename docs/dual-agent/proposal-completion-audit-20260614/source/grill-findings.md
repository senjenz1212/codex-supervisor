# PRD Grill Findings: Proposal Completion Audit

### Finding 1: Completion Can Be Overclaimed Without Liveness Evidence

status: resolved

Concern: The original request asks whether the auto-improvement work is done. Code presence alone is insufficient because the loop can be implemented but still not live if drafts are never activated, proposals are never approved, or daemon cadences do not fire.

Resolution: P1 and P3 now require a four-way classification: implemented, partial, missing, or live-unproven. The audit must read daemon wiring, AXI queue state, and trend rows before using the word complete.

### Finding 2: Report-Only Safety Needs Explicit Mutation Boundaries

status: resolved

Concern: Auditing proposal state could accidentally become an operator action if the lead runs activate, approve, deny, or apply commands while trying to inspect the system.

Resolution: P2 makes the mutation boundary a first-class promise. The only allowed commands are read-only status, list, trend, doctor, source inspection, and hash checks. Any policy overlay mutation or proposal state transition violates the PRD.

### Finding 3: Gate Compliance Must Be A Deliverable, Not Assumed

status: resolved

Concern: The previous local audit did not prove that the supervisor rigorous gate accepted it. A final answer that says "passed" without terminal gate evidence would repeat the exact credibility gap the program is meant to close.

Resolution: P4 requires terminal workflow evidence from `poll`, `catch-up`, transcripts, and reviewer decisions. If the workflow remains blocked, the report must say blocked and name the exact gate.

### Finding 4: Trend Metrics Can Mislead Without Denominators

status: resolved

Concern: Incident share by era can look like a reliability rate while actually measuring only the fraction of incident rows in one era. That would make D1 appear decided when it is undecidable.

Resolution: P3 forbids treating incident share as incident probability. The audit must explicitly say whether rates are normalized per run or per poll before recommending CLI migration.
