# TDD Grill Findings

### Finding 1

status: resolved

The TDD plan needed to start at report boundaries. All tests now drive official replay or official live runners and inspect emitted report rows, arm summaries, and matched-TAR status before relying on resolver internals.

### Finding 2

status: resolved

The valid receipt and hash-mismatch cases must share the same official replay path to prove the resolver is not bypassed. The tests use the same prediction schema and vary only the receipt evidence.

### Finding 3

status: resolved

Live official diagnostics had a decision-source compatibility risk that fixture replay would not catch. The live runner test now checks the exact synthesized source and the producer runner label in downstream bridge output.

### Finding 4

status: resolved

The TDD plan needed an adversarial live-generator case, not only the happy baseline generator case. A dedicated public-boundary test now proves supervisor-supplied baseline receipt fields are ignored.
