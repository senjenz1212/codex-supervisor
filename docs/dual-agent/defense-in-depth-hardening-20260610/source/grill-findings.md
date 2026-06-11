# PRD Grill Findings: Defense-In-Depth Hardening

### Finding 1: Rubric must not become a brittle model-only gate

Status: resolved

The requested Decision Runtime rubric could become unavailable or nondeterministic in tests. The PRD now requires deterministic substance scoring as the first implementation, records replayable reasons, and reserves the model-output field for future runtime use. This preserves the gate-hardening promise without introducing a hidden live-model dependency.

### Finding 2: Evidence-grade changes must not secretly enable or require fan-out

Status: resolved

The scope says do not change fan-out/concurrency defaults. The PRD now states that `agentic_lead.policy` remains unchanged and only the effective grade floor changes when fan-out worker receipts are already present.

### Finding 3: no-mistakes remains secondary evidence

Status: resolved

The PRD now says no-mistakes is post-acceptance defense-in-depth, never primary gate authority. Malformed output can block only when the operator configured the external service as required.

### Finding 4: Tamper detection needs a clear operator-facing reason

Status: resolved

The PRD now requires a dedicated red probe reason for sha256 and output-hash mismatches instead of relying on generic missing receipt language. That makes incident review and trend metrics more legible.
