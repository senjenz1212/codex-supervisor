# Harness v1 Architecture

## Evidence Kernel

`ClaimGate` is a deep module at the report-authorization seam. Callers provide
one evidence bundle and receive:

- the maximum cumulative claim level;
- derived improvement-claim flags;
- validation of registered report claims; or
- a hard error for manual managed fields or unsupported claims.

The interface is deliberately smaller than the evidence logic it hides:

```python
ClaimGate.max_claim_level(evidence_bundle)
ClaimGate.derived_claim_flags(evidence_bundle)
ClaimGate.validate_report(report, evidence_bundle)
ClaimGate.derive_report(report, evidence_bundle)
```

No caller supplies a desired level.

## Evidence Bundle

The canonical fields are defined in `claim-ladder.yaml`. Every evidence record
contains stable references and SHA-256 identities. A matching hash proves byte
identity, not meaning. ClaimGate therefore resolves and validates the
versioned JSON behind L3-L6 references. Bare booleans, opaque files, and
unparsed receipts are insufficient.

Levels are cumulative. The implementation evaluates predicates in order and
returns immediately at the first missing or malformed level. A bundle without
L0 evidence has no authorized claim level.

### L3: preregistered assignment authority

The powered B-vs-C design commits the assignment-key hash, assignment
protocol version, exact experiment-spec hash, distinct A/B/C treatment hashes,
and a canonical task-to-stratum manifest hash before execution. ClaimGate
recomputes every assignment from the revealed key and rejects a document whose
version, spec, treatment, roster, stratum, position, or HMAC-derived order
differs from those commitments. Rewriting all assignments self-consistently
after results exist is therefore insufficient: the rewritten protocol no
longer matches the preregistered powered design.

### L4: semantic replication

Portability requires a
`supervisor-strata-replication-analysis/v1` document bound to the source L3
analysis. It must reference at least three separate
`supervisor-b-vs-c-analysis/v1` studies. ClaimGate reruns the complete L3
validator for every study, rejects reused experiment IDs or overlapping task
sets, and derives strata and model families from each study's pinned
`replication_context`.

At least two strata, three model families, and one optimizer-unseen family are
required. The declared summary must exactly match those derived studies.
`replicated: true` plus a hash does not authorize L4.

### L5: preregistered value and complete incremental cost

ROI requires `supervisor-roi-analysis/v2`. Its business value comes from a
frozen `supervisor-business-value-protocol/v1` registered before the first
experimental execution. The protocol fixes the outcome metric, USD value per
verified success, decision horizon, and decision rule before results exist.

Cost comes from
`supervisor-incremental-cost-provenance/v1`. Compute cost is recomputed from
token usage and pinned rates, latency cost from elapsed time and its USD rate,
and risk cost from probability times impact. All three components are
mandatory for both arms and must reconcile exactly to baseline, supervisor,
and incremental totals. ClaimGate then recomputes successes, incremental
value, break-even value, and net value. Post-hoc value selection, missing risk
cost, or a self-declared `positive_roi` leaves the bundle at L4.

### L6: controlled candidate promotion

L6 requires six versioned, hash-linked receipts for one candidate change:

1. frozen control;
2. sealed holdout with an access-log identity;
3. non-regressing shadow result;
4. tested rollback that restores the frozen control;
5. approval by a named human identity and role; and
6. a partial-traffic, non-regressing canary.

ClaimGate checks shared change and policy identities, receipt-to-receipt hash
links, ordering, sample bounds, and zero declared guardrail regressions.
Removing or changing any receipt fails closed to L5.

L6 is authorization evidence for that candidate's governed promotion. It is
not proof that arbitrary future mutations are safe, and ClaimGate does not
deploy, approve, canary, or roll back anything itself.

## Report Production

New report producers should use `derive_report` and pass a standardized
evidence bundle. Producer input containing
`improvement_claim_allowed` or `powered_improvement_claim_allowed` at any depth
is rejected.

Existing report-only benchmark producers have been migrated away from literal
assignments. They obtain both flags from `ClaimGate.derived_claim_flags()` with
no L3 bundle, preserving their existing false authority while making the
source of that authority explicit. Future efficacy reports must pass their
actual evidence bundle rather than changing those constants.

Report consumers may display a derived flag but do not gain authority by
copying it. The evidence bundle and claim-gate receipt are the authorization
source.

## Claim Registry

`claims.yaml` is the program registry. The code carries the same minimum-level
rules so installed runtime packages do not depend on a repository-relative
documentation path. Focused tests compare the YAML registry and runtime rules
to prevent drift.

Governed `claims` entries and explicit claim-bearing fields such as
`claim_text`, `outcome_claim`, and `roi_claim` accept registered claim IDs
only. Display text comes from the registry's canonical text, so a producer
cannot attach stronger prose to a weaker ID. Ordinary diagnostic prose is
still scanned for registered regular-expression tripwires, but remains
untrusted narrative rather than claim authority; it is not a semantic
paraphrase classifier and must never be rendered as an authorized claim.

## Trust Boundaries

- Agent-visible workspaces do not contain hidden-verifier material.
- Report prose is untrusted.
- A hash without its referenced and schema-valid artifact is not sufficient.
- A powered statistic over one shared fixed candidate pool is not a generated
  B-vs-C efficacy experiment.
- Named-human approval is mandatory L6 evidence, but the approval system remains
  operationally separate from ClaimGate.
- Timestamps and identities are validated inside pinned receipts; stronger
  non-repudiation still depends on the evidence ledger and external anchors.
- Each event-hash schema resolves to exactly one frozen redaction ruleset.
  Historical verification never accepts a payload merely because any newer or
  older known redactor leaves it unchanged.
- If terminal persistence fails after a passing grade is appended, the
  passing revision is replaced, invalidated, or emergency-quarantined before
  it can authorize a decision.
- Later TRACE, LEDGER, and GRADE slices make evidence lineage immutable and
  queryable; PROGRAM-001 establishes the claim semantics they enforce.
