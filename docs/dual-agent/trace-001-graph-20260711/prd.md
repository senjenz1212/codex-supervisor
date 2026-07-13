# PRD: TRACE-001 Versioned Trace Graph

## Problem

Free-form IDs and document links cannot prove that a promotion is backed by a
complete Objective-to-Decision chain. Local IDs can collide, evidence can be
unbound to a run, and waivers can outlive their intended scope.

## Goal

Represent program evidence as a typed, namespaced, versioned graph and block
planning or promotion when the required chain is open.

## Promise Contracts

### P1: Every node has collision-resistant identity

- Public boundary: `TraceIdentity`.
- Identity contains namespace, typed logical ID, SHA-256 revision hash, and
  UUID-compatible instance ID.
- Forbidden: accepting a bare legacy ID without an explicit namespace.

### P2: Graph vocabulary is typed and provenance-aware

- Public boundary: `TraceNode`, `TraceEdge`, and `TraceGraph`.
- Nodes cover OBJ/CLAIM/REQ/ADR/ISSUE/TEST/TASK/EXP/ASN/RUN/ART/GRADE/ANL/
  DEC/POL/DEP and promotion activity.
- Edges use the controlled relation vocabulary and nodes map to W3C PROV
  entity/activity/agent concepts.

### P3: Closure fails closed

- Public boundary: `TraceGraph.validate_closure`.
- Requirements need tests; tests need assignment, pinned run, and runtime
  evidence; runtime evidence needs a verifier-pinned grade; decisions need
  analysis; promotions need decisions.
- Uncovered nodes block unless an exact, signed, unexpired waiver applies.

### P4: Promotion trace is queryable

- Public boundary: `TraceGraph.promotion_trace`.
- A promotion query returns the canonical path through decision, analysis,
  grade, artifact, run, assignment, test, requirement, and objective.

### P5: Planning honors closure

- Public boundary: `validate_planning_artifacts`.
- Supplying a graph adds a fail-closed TRACE-001 check with an explicit aware
  evaluation time.

## Non-goals

- Claiming current runtime artifacts are already registered in the graph.
- Defining a durable graph database in this slice.
- Treating HMAC waiver authentication as a public-key signature.
