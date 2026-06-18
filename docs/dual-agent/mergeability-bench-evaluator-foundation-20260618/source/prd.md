## Problem Statement

Supervisor AutoResearch can draft experiments, execute hash-pinned evaluators, validate quality controls, and produce human-approved proposals, but its default metric source is still replay-oriented. That replay path checks existing evidence references rather than whether a candidate would actually be mergeable. The auto-improvement loop therefore needs a deterministic held-out mergeability bench that can reject no-op, harmful, out-of-scope, or test-gaming candidates before policy proposals are treated as evidence of real improvement.

## Solution

Add a local mergeability bench foundation with typed task, candidate, and result records; a toy held-out corpus with no-op, known-bad, and known-good controls; and a deterministic grader that runs in an isolated validation workspace. Hidden behavioral tests, reverse-classical tests, mutable-path constraints, and optional lint or build commands become the primary oracle. Weighted reviewer-style rubric fields are recorded as secondary evidence only.

## User Stories

As a supervisor operator, I want AutoResearch reports to distinguish real improvement from metric gaming so that accepted proposals are grounded in repeatable evidence. As a workflow maintainer, I want no-op and harmful candidates to fail before they can become policy evidence. As a researcher, I want the same held-out bench to support future false-accept experiments at matched true-accept without changing the underlying generator.

## PRD Promise Contracts

P1. The mergeability bench exposes a stable public API that loads task fixtures, validates candidates, and returns typed results with deterministic blocker status and replayable evidence references.

P2. The grader executes hidden tests, reverse-classical tests, scope checks, and optional lint or build commands in an isolated workspace where candidates cannot modify answer keys or evaluator files.

P3. The AutoResearch evaluator integration computes metric trials from real mergeability grading, emits supervisor-owned runtime-native evidence, and preserves report-only invariants without advancing gates or mutating policy.

P4. Calibration controls prove the evaluator is discriminating: no-op and known-bad candidates fail, known-good candidates pass, and saturated fixture-only metrics remain non-applyable.

## Implementation Decisions

The first slice uses small local Python fixtures instead of public SWE-bench, Terminal-Bench, or paid FrontierCode execution. The fixture corpus lives under `tests/fixtures/mergeability_bench/` and is designed for expansion rather than broad statistical claims. The grader applies candidate files into a temporary copy of the fixture repository, runs commands with argv-form subprocess execution, and records artifact hashes for task definitions, candidate payloads, command outputs, and result payloads.

## Testing Decisions

Public-boundary tests exercise the loader, deterministic grader, evaluator script, and AutoResearch validation path rather than private helpers alone. The first tests prove no-op, known-bad, and known-good controls behave differently on the same task shape. Additional tests prove candidate edits to hidden materials are rejected, mutable-path escapes are rejected, and the evaluator produces computed metric trials with runtime-native evidence while keeping report-only invariants false.

## Out of Scope

This slice does not run a powered multi-model study, expand the reviewer roster, build provider-neutral lead generation, or connect live external benchmarks. It does not auto-apply policy changes, treat LLM rubric output as an oracle, or claim supervisor accuracy has improved. It creates the trusted evaluator substrate that later slices can use for those experiments.
