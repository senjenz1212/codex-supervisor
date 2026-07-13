# Issues

## ISS-1: Cumulative ClaimGate Ladder

Type: AFK
Priority: P0
Blocked by: None
PRD promise: P1, P2

Build the public claim-level seam from L0 integrity through L6
auto-improvement, with cumulative fail-closed predicates.

First public-boundary RED test:
`test_fixture_replay_bundle_resolves_to_l1`.

Acceptance Criteria:

- [ ] Fixture replay with pins, hashes, artifacts, and detector resolves L1.
- [ ] Independent hidden verification is mandatory for L2.
- [ ] Positive randomized powered B-vs-C evidence is mandatory for L3.
- [ ] Replication, ROI, and auto-improvement controls gate L4–L6.

## ISS-2: Derived Flags and Forbidden Claim Validation

Type: AFK
Priority: P0
Blocked by: ISS-1
PRD promise: P3, P4

Make `ClaimGate` the only authority for improvement flags and reject claims
above the evidence level.

First public-boundary RED test:
`test_report_producer_cannot_manually_set_improvement_claim_flag`.

Acceptance Criteria:

- [ ] Both managed flags derive false below L3 and true at L3+.
- [ ] Top-level and nested producer overrides raise `ManualClaimFlagError`.
- [ ] Fixture L1 evidence cannot assert L3.
- [ ] "Supervisor improves outcomes" is rejected below L3.
- [ ] Registered L3 claim passes with qualifying evidence.

## ISS-3: Producer Migration and Program Pack

Type: AFK
Priority: P1
Blocked by: ISS-2
PRD promise: P3, P5

Route production report-only flag assignments through `ClaimGate`, add the
machine-readable program docs, and map legacy artifacts without rewriting
them.

First public-boundary RED test:
`test_producers_do_not_literal_assign_claim_gate_owned_flags`.

Acceptance Criteria:

- [ ] Production Python contains no literal boolean assignment to either
  managed field.
- [ ] Existing report-only tests retain false authority flags.
- [ ] Claim ladder and claim registry YAML parse and match runtime rules.
- [ ] Program pack and five PRD→TDD artifacts exist.
- [ ] Legacy references carry evidence-authority caveats.
