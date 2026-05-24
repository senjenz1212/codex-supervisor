# Good Grill Findings Fixture

## Findings

### Finding G1

status: resolved
severity: high
question: Could section-only documents pass?
resolution: Fixture matrix includes sneaky files that have headings but shallow
content, and the validator blocks them deterministically.

### Finding G2

status: waived
severity: medium
question: Should exact skill-run provenance be mandatory now?
reason: The current slice verifies durable artifacts. Skill-run provenance is a
separate future receipt and should not block this validator fixture.

## Decision

No open findings remain.
