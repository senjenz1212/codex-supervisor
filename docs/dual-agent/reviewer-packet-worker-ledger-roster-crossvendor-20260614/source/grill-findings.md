# PRD Grill Findings

## Findings

### Finding G1: Packet Tests Must Hit Reviewer Invocation, Not Only Helpers

Status: resolved

- Risk: a pure ReviewPacket helper can pass while live reviewers still receive
  free-form implementer context.
- Resolution: TDD requires both packet helper validation and workflow review
  invocation tests that assert the packet/context receipt participates in gate
  adjudication.

### Finding G2: Cross-Vendor Policy Needs Explicit Degraded Semantics

Status: resolved

- Risk: if only one provider is configured, diversity can be faked or silently
  skipped.
- Resolution: TDD requires both different-provider selection and unavailable
  provider behavior: block when reviewer_unavailable_policy=block, otherwise
  emit degraded_review_unavailable.

### Finding G3: Runtime Receipts Cannot Be Trusted By String Stamp

Status: resolved

- Risk: reviewer packet validation could accept caller-stamped
  source=supervisor/evidence_grade=runtime_native receipts.
- Resolution: packet validation must correlate runtime receipts with
  supervisor-originated runtime evidence for the current gate invocation.

### Finding G4: Worker Ledger Must Not Create A Second Truth Store

Status: resolved

- Risk: a roster/session JSON file could become an accidental registry.
- Resolution: files may be output/transcript artifacts only; worker dispatch and
  roster facts must be ledger events with replayable hashes.

## Disposition

All findings are resolved in the TDD plan. No findings are waived.
