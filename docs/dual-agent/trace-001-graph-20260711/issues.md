# Issues: TRACE-001

## T1: Namespaced versioned identities

- PRD promises: P1, P2
- Public boundary: `TraceIdentity`
- Blocked by: PROGRAM-001 terminology
- Acceptance:
  - Bare legacy IDs are rejected.
  - Two `P1` requirements in different namespaces do not collide.
  - Revision and instance identity are validated deterministically.

## T2: Canonical closure rules

- PRD promises: P3, P4
- Public boundary: `TraceGraph.validate_closure`
- Blocked by: T1
- Acceptance:
  - A complete promotion path passes and is queryable.
  - Every missing link named in the PRD produces a stable finding.
  - Runtime evidence requires a pinned run and verifier-pinned grade.

## T3: Exact expiring waivers

- PRD promises: P3
- Public boundary: `TraceWaiver.sign` and `validate_closure`
- Blocked by: T2
- Acceptance:
  - A current waiver applies only to its exact node and closure rule.
  - Expired, wrongly signed, wrong-node, and wrong-rule waivers fail closed.

## T4: Planning and runtime integration

- PRD promises: P5
- Public boundary: `validate_planning_artifacts`
- Blocked by: T2, T3
- Acceptance:
  - Broken closure blocks planning validation.
  - Closed closure passes.
  - Runtime producers persist graph nodes/edges for actual runs rather than
    constructing only in-memory test graphs.
