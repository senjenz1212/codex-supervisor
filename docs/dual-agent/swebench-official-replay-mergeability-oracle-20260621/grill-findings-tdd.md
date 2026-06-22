### Finding 1

Status: resolved

The TDD plan now starts at public boundaries: the official replay function and CLI refusal path. Helper logic such as manifest normalization or oracle result parsing is only exercised through those public tests.

### Finding 2

Status: resolved

Every PRD promise has a named test mapping. The hidden-oracle promise is tested at packet, reviewer packet, and frozen decision surfaces, which are the surfaces that would make oracle coupling dangerous.

### Finding 3

Status: resolved

The plan avoids claiming real Docker execution in CI. It requires oracle_adapter_kind labeling, so equivalent adapters remain useful for deterministic tests without overstating benchmark fidelity.
