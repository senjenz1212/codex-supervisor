# TDD Grill Findings

### Finding 1: Missing-test and unresolved-name paths must be separate

Status: resolved

Risk: A typoed test name could be reported as merely missing, making the operator chase the wrong problem.

Resolution: Keep `tdd_test_names_unresolved` separate from `tdd_tests_not_executed`.

### Finding 2: Era trend output needs JSON and TOON coverage

Status: resolved

Risk: JSON-only assertions leave the default operator view blind.

Resolution: Add `test_axi_trends_surfaces_by_era_in_json_and_toon`.

### Finding 3: Format metrics must be emitted by the live CLI path

Status: resolved

Risk: Aggregation tests can pass even when live poll output records no samples.

Resolution: Add `test_axi_toon_poll_records_format_metric`.

### Finding 4: TDD plan must name every new test

Status: resolved

Risk: The slice could introduce a TDD floor that it does not satisfy itself.

Resolution: The TDD plan names all baseline, TDD-floor, trend, poll, Postgres, format, and lesson-injection tests.

### Finding 5: Advisory lessons must not become standalone gate authority

Status: resolved

Risk: A prior FM-1.3 lesson can cause the lead to block a fresh run even when current evidence says the gate artifact is sound.

Resolution: Add `test_lesson_injection_says_lessons_are_not_standalone_gate_decisions`.
