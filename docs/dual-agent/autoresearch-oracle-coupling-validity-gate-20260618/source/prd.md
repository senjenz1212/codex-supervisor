# AutoResearch Oracle Coupling Validity Gate PRD

## Problem Statement

The mergeability paired pilot currently presents an oracle-derived acceptance arm beside the baseline arm, which makes the reported false-accept reduction circular. The benchmark remains useful as a calibration and plumbing check, but it cannot honestly support a claim that Supervisor independently reduced false accepts. The gate system previously recorded the concern as non-blocking, so the next slice must convert that lesson into blocking report metadata.

## Solution

Add explicit measurement-validity metadata to paired reports and policy-derivation checks. Every acceptance arm will declare its role, decision source, and whether the source is coupled to the oracle, hidden grader, expected outcome, or final score. Oracle-coupled treatment evidence will remain exportable for replay, while the report itself becomes non-applyable and forbidden from making improvement claims or producing policy proposals.

## User Stories

As a Supervisor operator, I want reports to distinguish independent supervisor evidence from oracle ceilings so improvement claims are not circular. As an AutoResearch maintainer, I want seeded calibration artifacts to remain replayable while clearly marked as non-applyable. As a reviewer, I want tests that fail when an oracle-derived treatment arm is promoted as evidence of real gate performance.

## PRD Promise Contracts

P1. Paired acceptance reports expose arm role, decision source, oracle coupling, report label, applyability, improvement-claim permission, and gaming flags through the public report boundary.

P2. The current oracle-derived acceptance arm is labeled as an oracle ceiling and cannot be interpreted as independent Supervisor acceptance or policy-improvement evidence.

P3. Policy proposal derivation rejects any AutoResearch or mergeability report record with oracle-coupled treatment evidence, disabled metric applyability, or disabled improvement claims.

P4. Existing calibration value is preserved: seeded baseline false accepts remain visible, oracle ceiling metrics remain replayable, and report-only authority invariants remain unchanged.

## Implementation Decisions

The first implementation stays inside deterministic report construction and validation paths. It will not run live agents, execute real Supervisor gates per candidate, expand the corpus, or alter gate authority. Declared decision-source provenance is the primary blocking signal, while exact treatment-oracle equality is recorded as a suspicious fallback flag when independent provenance is absent or insufficient.

## Testing Decisions

Tests start at the `run_paired_acceptance_pilot` public boundary and at the policy proposal derivation boundary. The former proves metadata, relabeling, seeded baseline false accepts, and non-applyable oracle ceiling behavior. The latter proves oracle-coupled reports cannot create applyable policy proposals. Existing AutoResearch report-only invariant tests remain in place as regression coverage.

## Out of Scope

This slice does not implement independent Supervisor gate execution for each candidate, live candidate generation, powered factorial experiments, expanded benchmark tasks, policy mutation, or automatic gate advancement. It also does not claim that the current paired pilot measures production false-accept reduction. Those tasks require this validity gate first, then separate measurement slices with independent acceptance evidence.
