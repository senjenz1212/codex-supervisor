# PRD Grill Findings

### Finding 1: Prompt-Only Compliance Is Not Enough

Status: resolved

The previous execution contract lived in the lead prompt. A capable or mistaken lead can
still return an accept-shaped outcome without real edits, so the PRD must require a
deterministic probe wired into the gate decision.

Resolution: P1 and P2 require `P11` deliverable evidence at the workflow boundary.

### Finding 2: Report-Only Must Be Explicit

Status: resolved

Docs/report artifacts are legitimate for ADRs and report-only evals, but arbitrary
docs-only edits must not satisfy a code implementation task.

Resolution: P4 requires explicit report-only/docs-only scope text plus a covering
artifact receipt.

### Finding 3: Generated Workflow Files Are Incidental

Status: resolved

The workflow always produces transcripts, interactions, source planning docs, and
handoff files. Counting those as deliverables would make the new gate vacuous.

Resolution: P3 excludes generated dual-agent source, transcript, replay, handoff, and
scratch paths from deliverable evidence.
