# PRD Grill Findings: Reviewer Panel Second Reviewer

### Finding 1: Route choice must be evidence-based

Concern: The PRD could pick a second reviewer by preference and leave the
blocking open question unresolved.

Resolution: The PRD includes the live route evidence. Cursor SDK with
`composer-2.5` and `gpt-5.5` returns infrastructure unavailable; the Codex
CLI/GPT-family route returns a typed verdict under read-only sandbox.

Status: resolved

### Finding 2: Agentic assurance can be overstated

Concern: A JSON/text reviewer could be mislabeled as agentic simply because it
uses a strong model.

Resolution: P2 requires `assurance_grade=agentic` only when tool-backed
read-only execution, transcript refs, and hashes are present. Text-only routes
remain `text_only`.

Status: resolved

### Finding 3: Degraded recovery must not weaken a real block

Concern: Proceed-degraded recovery for one unavailable reviewer could bypass a
real revise/deny from another reviewer.

Resolution: P4 forbids degraded recovery from overriding real revise/deny, and
P3 keeps the existing conservative hard-block semantics.

Status: resolved
