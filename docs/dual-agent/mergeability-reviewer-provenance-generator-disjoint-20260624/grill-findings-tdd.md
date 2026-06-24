# TDD Grill Findings

## Finding 1: Helper-Only Tests Would Miss The Risk

Status: resolved.

The TDD plan starts at `run_paired_acceptance_pilot`, where operators actually read roster evidence. Helper behavior is covered only through public report assertions.

## Finding 2: Same-Family Warning Needs Produced Generator Metadata

Status: resolved.

The same-family test uses produced single-agent baseline decisions with explicit provider-family metadata. The implementation must not infer generator family from ordinary fixture candidate metadata.

## Finding 3: Text-Only Must Not Be Overwritten By Self-Reported Assurance

Status: resolved.

The TDD plan pins LiteLLM structured reviewers as text-only unless command evidence exists, even if the reviewer result carries generic tool-like assurance text.

## Finding 4: Bounded Runner Must Not Drift

Status: resolved.

The implementation mirrors the shared provenance/disjointness helpers into the bounded runner report so the long-running corpus path does not diverge from the paired pilot.
