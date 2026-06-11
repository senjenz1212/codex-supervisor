# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 655292 `prd_review`: gate blocked
- event_id 655494 `prd_review`: both agents accepted
- event_id 655594 `issues_review`: S3-AC3: activation must emit ledger evidence it was operator-triggered with no policy mutation or gate advance; source generator.py:203-218 sets operator/automatic_policy_mutation=False/gate_advanced=False but no test asserts the activation event payload
- event_id 655594 `issues_review`: S4-AC4: failed-run deterministic terminal status + ledger event (generator.py:297-317) has no dedicated test; only completed path tested at :208-214
- event_id 655594 `issues_review`: S2-AC2 names budget/timeout but draft-row test does not assert them on the row (set at generator.py:124-125; config-load asserted at :85-89)
- event_id 655595 `issues_review`: both agents accepted
- event_id 655668 `tdd_review`: tdd.md enumerates only the 6 generator tests and omits the schema migration v9 / Postgres Alembic parity tests that PRD Testing Decisions and Slice 1 explicitly require; the coverage demonstrably exists at test_schema_migrations.py:299 and test_postgres_ledger_lane.py:104-140, so this is a plan-completeness/traceability nit, not a missing-coverage blocker
- event_id 655668 `tdd_review`: No dedicated negative test asserts the activation ledger-evidence payload (automatic_policy_mutation=False, gate_advanced=False) for Slice 3 AC3; source-enforced at generator.py:203-218, non-blocking
- event_id 655668 `tdd_review`: Tests are currently GREEN not RED because implementation already exists; the RED state is described hypothetically in the plan rather than demonstrated
- event_id 655863 `tdd_review`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 655869 `tdd_review`: tdd.md enumerates only the 6 generator tests and omits the schema migration v9 / Postgres Alembic parity tests that PRD Testing Decisions and Slice 1 explicitly require; the coverage demonstrably exists at test_schema_migrations.py:299 and test_postgres_ledger_lane.py:104-140, so this is a plan-completeness/traceability nit, not a missing-coverage blocker
- event_id 655869 `tdd_review`: No dedicated negative test asserts the activation ledger-evidence payload (automatic_policy_mutation=False, gate_advanced=False) for Slice 3 AC3; source-enforced at generator.py:203-218, non-blocking
- event_id 655869 `tdd_review`: Tests are currently GREEN not RED because implementation already exists; the RED state is described hypothetically in the plan rather than demonstrated
- event_id 655901 `tdd_review`: P4 forbidden outcome 'status remains ambiguous after a run failure' (prd.md:77-78) and Slice 4 AC#4 'failed rows have deterministic terminal statuses' (issues.md:75-76) are implemented (generator.py:297-317) but covered by zero tests; add a RED failure-path test before advancing
- event_id 655901 `tdd_review`: NIT: P2 forbidden 'activation lacks operator/channel metadata' has no negative test
- event_id 655902 `tdd_review`: agents have not both accepted yet; revise and continue
- event_id 655904 `tdd_review`: P4 forbidden outcome 'status remains ambiguous after a run failure' (prd.md:77-78) and Slice 4 AC#4 'failed rows have deterministic terminal statuses' (issues.md:75-76) are implemented (generator.py:297-317) but covered by zero tests; add a RED failure-path test before advancing
- event_id 655904 `tdd_review`: NIT: P2 forbidden 'activation lacks operator/channel metadata' has no negative test
- event_id 656100 `tdd_review`: both agents accepted
- event_id 656348 `implementation_plan`: cursor_review_failed: P2 test fails: metric_source=pending, validation_status=rejected, runner still marks completed despite grill Finding 3 requiring accepted report + durable evaluator proof
- event_id 656363 `implementation_plan`: generator.py:274-280 stamps 'completed' with no metric_source/validation_status check; only an exception reaches the 'failed' path (:297-317).
- event_id 656363 `implementation_plan`: orchestrator.py:147-162 catches EvaluatorContractError/TimeoutError, marks attempt failed but does NOT re-raise, then returns a normal report (:188,:212) - defeating the runner's exception-only failure path.
- event_id 656363 `implementation_plan`: validation.py:100-106 rejects any attempt whose metric_source!='evaluator_execution', so a swallowed evaluator failure deterministically produces validation_status='rejected' yet the queue row is 'completed'.
- event_id 656363 `implementation_plan`: No green pytest receipt obtained (pytest approval-blocked); P2 is RED per corrective context.
- event_id 656364 `implementation_plan`: agents have not both accepted yet; revise and continue
- event_id 656366 `implementation_plan`: generator.py:274-280 stamps 'completed' with no metric_source/validation_status check; only an exception reaches the 'failed' path (:297-317).
- event_id 656366 `implementation_plan`: orchestrator.py:147-162 catches EvaluatorContractError/TimeoutError, marks attempt failed but does NOT re-raise, then returns a normal report (:188,:212) - defeating the runner's exception-only failure path.
- event_id 656366 `implementation_plan`: validation.py:100-106 rejects any attempt whose metric_source!='evaluator_execution', so a swallowed evaluator failure deterministically produces validation_status='rejected' yet the queue row is 'completed'.
- event_id 656366 `implementation_plan`: No green pytest receipt obtained (pytest approval-blocked); P2 is RED per corrective context.
- event_id 656406 `implementation_plan`: Tests exist, are non-vacuous, and reference real symbols, but pytest was policy-DENIED so unconfirmed-green; test_status remains unknown/self_reported
- event_id 656406 `implementation_plan`: Artifact sha256 verification (shasum/hashlib) DENIED; handoff fidelity self_reported, though content read-verified from declared paths
- event_id 656761 `implementation_plan`: both agents accepted
- event_id 656799 `execution`: both agents accepted
- event_id 656805 `outcome_review`: required_artifacts_missing
- event_id 656891 `outcome_review`: Live poll of workflow-1bdbea5238c1 was permission-denied; durable execution of this specific job is unconfirmed, mitigated by test :213 (workflow_job 0->1 post-activation) + handoff required_evidence_grade=self_reported.
- event_id 656891 `outcome_review`: pytest not executed (approval required) so the 7 tests are unconfirmed-green though present, non-vacuous, and referencing real symbols.
- event_id 656891 `outcome_review`: shasum -a 256 denied so artifact-hash match vs handoff is self_reported; content read-verified from declared paths instead.
- event_id 657102 `outcome_review`: both agents accepted
