# TDD Grill Findings

Task id: `powered-factorial-runner-20260626`

## Finding 1: First proof must be the production runner, not the math helper.

Resolution: The first RED test invokes the CLI. Existing math tests remain supporting coverage.

## Finding 2: The runner can accidentally pass by using a generated local corpus.

Resolution: Tests assert the report records the original Pro predictions path and candidate artifact hashes. The adapter manifest is not treated as the source corpus.

## Finding 3: Small-threshold tests could overclaim real powering.

Resolution: Tests may lower thresholds to prove behavior, but the report records the thresholds and the default CLI floors remain 30/30/25/0.05.

## Finding 4: Authority flags can drift when the core evaluator marks `metric_applyable`.

Resolution: The TDD plan asserts mutation and improvement flags remain false, and the contract is the only conversion-readiness signal in this slice.
