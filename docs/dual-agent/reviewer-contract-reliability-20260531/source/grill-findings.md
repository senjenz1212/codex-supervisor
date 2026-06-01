# Reviewer Contract Reliability PRD Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Is the problem prompt non-compliance, parser strictness, or runtime
infrastructure?
resolution: Phase 0 showed Cursor SDK plan and agent modes returned errored,
empty transcripts with 0/8 typed outcomes. Parser tolerance cannot recover a
missing transcript.

### Finding G2

status: resolved
severity: high
question: Does the proposed default preserve independent review rather than
turning into another Claude review?
resolution: The default route is Gemini 3.1 Pro through LiteLLM, while Claude
Code remains the `/lead` implementer. Claude-via-LiteLLM is not the default.

### Finding G3

status: resolved
severity: high
question: Does structured JSON output weaken the typed
`dual_agent_outcome` contract?
resolution: No. The structured route must wrap the returned JSON only for
validation and still pass the existing `evaluate_outcome_fidelity` plus
critical-review completeness checks.

### Finding G4

status: resolved
severity: high
question: What prevents a real reviewer rejection from being treated as
unavailable infrastructure?
resolution: A parseable `revise` or `deny` outcome remains a real reviewer
verdict and stays on the existing blocking path. Recovery applies only to
recoverable infrastructure or contract-unmet classifications.

### Finding G5

status: resolved
severity: medium
question: Could live gateway access make tests flaky?
resolution: Live probes are durable artifacts. Unit and workflow tests mock the
gateway below the public reviewer boundary and use fixtures for deterministic
replay.

## Decision

No open PRD grill findings remain. Proceed to issue slicing and TDD with the
LiteLLM Gemini structured route as the default reviewer path.
