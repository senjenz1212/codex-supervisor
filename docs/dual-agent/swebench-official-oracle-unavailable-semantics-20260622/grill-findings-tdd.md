# TDD Grill Findings

### Finding 1

status: resolved

The TDD plan originally risked testing only adapter helper shape. It now starts with the official replay runner boundary, proving emitted reports suppress metrics when oracle labels are unavailable.

### Finding 2

status: resolved

The plan needed a guard against over-broad unavailable semantics. A valid official unresolved report test is included so real benchmark failures still produce ordinary fail labels.

### Finding 3

status: resolved

Receipt validation could become an implementation-only assertion. The plan ties receipt acceptance to replay validation behavior and explicitly requires unavailable reason evidence for unavailable rows.
