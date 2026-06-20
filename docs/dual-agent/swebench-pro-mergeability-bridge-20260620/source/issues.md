# Issues

## Slice 1: Public SWE-bench Pro Mergeability Packets

priority: high

scope: Create the public packet path that converts SWE-bench Pro-shaped instance metadata and candidate patch metadata into a public-only mergeability input. The packet must include enough information for S_probe and reviewers while excluding all hidden oracle fields and hidden command strings.

PRD promise: P1, P2, P3, P6, P7.

Public boundary: `swebench_pro_mergeability_bridge_report`.

Chosen seam: a new deep bridge interface that accepts fixture-shaped instance records and candidate arm artifacts, then returns reportable public packets and oracle-isolation status.

acceptance criteria:

- [ ] Public packet includes instance id, repo, base commit, problem statement, public checkout hash or ref, candidate artifact hash, and explicit S_probe substrate metadata.
- [ ] Public packet excludes FAIL_TO_PASS, PASS_TO_PASS, test_patch, final_score, oracle_accept, expected_outcome, and hidden command strings.
- [ ] Forbidden oracle key or text in a public packet triggers an oracle-isolation failure and prevents acceptance.

Blocked by: None - can start immediately.

## Slice 2: Frozen Arm Decisions Before Oracle Grading

priority: high

scope: Record baseline, S_probe, and S_full decisions before hidden oracle outcomes are attached. S_probe uses static patch applicability plus curated public lint/build commands. S_full combines S_probe with an independent reviewer panel over a public-only packet.

PRD promise: P2, P3, P4, P5, P6.

Public boundary: `swebench_pro_mergeability_bridge_report`.

Chosen seam: the same bridge report interface, with arm decision inputs separated from oracle result inputs.

acceptance criteria:

- [ ] Report records a decision-phase marker before oracle outcomes.
- [ ] S_probe substrate is explicit and cannot be absent.
- [ ] Reviewer-panel unavailable makes S_full unavailable, not accepted or imputed.
- [ ] S_full can disagree with S_probe and the disagreement is preserved.

Blocked by: Slice 1.

## Slice 3: Hidden Oracle FAR/TAR/FRR Reporting

priority: high

scope: Attach held-out FAIL_TO_PASS/PASS_TO_PASS outcomes only after arm decisions are frozen, then compute FAR, TAR, FRR, matched-TAR status, panel marginal delta where computable, denominators, confidence intervals, PASS_TO_PASS regression status, and oracle ceiling labels.

PRD promise: P3, P5, P6.

Public boundary: `swebench_pro_mergeability_bridge_report`.

Chosen seam: report summarization over frozen decision rows and post-decision oracle labels.

acceptance criteria:

- [ ] FAR/TAR/FRR denominators are computed from hidden oracle labels only after frozen decisions.
- [ ] PASS_TO_PASS failures contribute to regression/no-regression reporting.
- [ ] Oracle ceiling is labeled as oracle-coupled and cannot be reported as supervisor improvement.
- [ ] Report-only invariants remain false and policy derivation returns no applyable proposal.

Blocked by: Slice 2.

## Slice 4: Existing SWE-bench Solve-Rate Non-Regression

priority: medium

scope: Preserve the existing SWE-bench Pro pass-at-k pilot and document that it remains a solve-rate instrument, not the mergeability FAR/TAR bridge.

PRD promise: P7.

Public boundary: existing SWE-bench Pro report builder and CLI tests.

Chosen seam: existing `build_swe_bench_report` behavior.

acceptance criteria:

- [ ] Existing SWE-bench pass-at-k tests remain green.
- [ ] Bridge documentation distinguishes solve-rate uplift from false-accept reduction.
- [ ] No existing pass-at-k report field is repurposed as FAR/TAR.

Blocked by: None - can run throughout implementation.
