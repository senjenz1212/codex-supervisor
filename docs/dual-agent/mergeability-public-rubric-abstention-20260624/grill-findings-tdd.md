# TDD Grill Findings

## Finding 1: Packet Tests Must Run Through The Report Boundary

Status: resolved.

The first test uses `run_paired_acceptance_pilot` instead of calling only the helper so packet construction, leak scanning, and report serialization remain covered together.

## Finding 2: Abstention Must Be Tested Against Aggregation

Status: resolved.

The abstention test uses the configured reviewer panel seam and verifies the full-gate arm, not merely the normalized reviewer result.

## Finding 3: Rubric Coverage Must Not Replace Oracle Scoring

Status: resolved.

The report test asserts the coverage block names deterministic oracle scoring as the authority and keeps LLM labels descriptive.
