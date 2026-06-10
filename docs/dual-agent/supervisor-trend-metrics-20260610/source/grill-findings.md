# PRD Grill Findings

### Finding 1

Status: resolved

Concern: "Quality metric" could accidentally become a new gate if written near existing P11/P12/P13 logic.

Resolution: The PRD names an observational-only invariant and requires metrics code to live behind a separate trend API/CLI with no gate mutation path.

### Finding 2

Status: resolved

Concern: A read query that recomputes from the entire ledger on every call would become slow and nondeterministic as history grows.

Resolution: The PRD requires a persisted trend table and a read-only query over that table.

### Finding 3

Status: resolved

Concern: "False accept" must be aligned with existing reviewer-panel vocabulary, not a one-off term.

Resolution: The PRD explicitly reuses `false_accept_count`, `false_accept_denominator`, and `false_accept_rate`.
