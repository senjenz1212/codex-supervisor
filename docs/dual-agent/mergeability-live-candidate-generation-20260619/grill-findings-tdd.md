# TDD Grill Findings

### Finding 1 - Tests must start at the report boundary

Status: resolved

Question: Are the first tests helper-only, or do they prove the public behavior that operators will trust?

Resolution: The TDD plan starts with `test_live_generation_requires_allow_live_before_generators_run` against the public live mergeability report interface. Helper-level derivation tests are allowed only after the report and evaluator manifest surfaces exist.

### Finding 2 - Fake generators are acceptable only at the external provider seam

Status: resolved

Question: Does using fake generators hide the supervisor behavior under test?

Resolution: The plan fakes only the external model provider adapter. Public task construction, hidden-material exclusion, candidate hashing, oracle grading, and policy derivation stay on real repository code paths.

### Finding 3 - Oracle isolation must be asserted on captured inputs

Status: resolved

Question: Could a test pass by trusting a helper name instead of proving the generator saw public-only material?

Resolution: The TDD plan includes a captured-input assertion that serializes generator inputs and checks for hidden commands, final scores, expected outcomes, oracle labels, protected path markers, and hidden fixture content.

### Finding 4 - Budget overrun cannot be a soft warning

Status: resolved

Question: What stops an expensive arm from still counting as an accepted result?

Resolution: The TDD plan requires overruns to mark the arm unavailable or rejected and forbids interpretation as an accepted improvement.

### Finding 5 - Evaluated-path derivation needs both sides

Status: resolved

Question: Does the plan prove the replacement for the constant handles both true and false cases?

Resolution: The TDD plan includes one public evaluator-manifest case for a non-evaluated change and one for an evaluated mutable path or control delta, ensuring the flag is not merely inverted or always true.
