# TDD Grill Findings

### Finding 1: Public Boundary Must Be The Bench API And Evaluator Script

Status: resolved

The TDD plan must not test only helper parsing because the PRD promises a reusable evaluator substrate. The first tests now call `load_mergeability_tasks`, `grade_mergeability_candidate`, and the executable AutoResearch evaluator script as public boundaries.

Resolution: incorporated into MB-1, MB-2, and MB-3 first RED tests.

### Finding 2: Reverse-Classical Coverage Needs Its Own Assertion

Status: resolved

A hidden-test-only evaluator can still miss candidate-supplied tautological tests. The TDD plan now includes a dedicated reverse-classical test proving candidate tests fail on the base fixture when the candidate change is necessary.

Resolution: incorporated as `test_reverse_classical_requires_candidate_tests_fail_on_base`.

### Finding 3: Runtime-Native Evidence Must Be Visible In The Evaluator Output

Status: resolved

AutoResearch integration would be too weak if it only returned a numeric metric. The TDD plan now requires the evaluator script to emit runtime-native receipt metadata and evidence refs so downstream reports can replay how the metric was produced.

Resolution: incorporated into MB-3 tests and regression commands.
