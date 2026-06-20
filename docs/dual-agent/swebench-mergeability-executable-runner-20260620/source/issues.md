# SWE-bench Mergeability Executable Runner Issues

## Slice 1 -- Add the fixture runner public interface

Priority: P0

Scope: Add a fixture-first runner interface that accepts a local SWE-like instance, candidate artifact, public probe commands, optional reviewer panel callable, and output directory. The runner prepares a public worktree, applies the candidate patch, records public command results, freezes arm decisions, runs oracle commands afterward, and calls the existing bridge report seam.

PRD promise: P1, P2, P3, and P4.

Public boundary for the first RED test: `swebench_mergeability_fixture_runner`.

Chosen seam/interface for the first RED test: runner entrypoint that emits a `report.json` and frozen decision artifact.

Representative action: Run the fixture runner against a tiny local fixture with one public command and one hidden oracle command.

Allowed outcomes: The report includes S_probe, S_full, baseline/oracle labels, frozen decision hashes, command receipts, and report-only invariants.

Forbidden outcomes: The runner must not read oracle files before decision freeze, must not impute unavailable S_full, and must not mutate policy.

Acceptance criteria:
- [ ] The runner emits a real `report.json` from locally executed public and oracle commands.
- [ ] The decision artifact is written before hidden oracle outcomes are read.
- [ ] The bridge report remains the sole FAR/TAR report assembly seam.

## Slice 2 -- Enforce public worktree and reviewer packet isolation

Priority: P0

Scope: Copy only public fixture files into the execution worktree, keep protected hidden oracle paths out of public command and reviewer contexts, and reuse the SWE-bench oracle leak detector for reviewer packets.

PRD promise: P2.

Public boundary for the first RED test: `swebench_mergeability_fixture_runner`.

Chosen seam/interface for the first RED test: public worktree builder and reviewer packet export produced by the runner.

Representative action: Run a fixture containing hidden oracle files and inspect the public worktree plus reviewer packet.

Allowed outcomes: Protected files remain absent before the freeze and leak attempts fail closed with explicit isolation metadata.

Forbidden outcomes: Hidden files, hidden commands, `FAIL_TO_PASS`, `PASS_TO_PASS`, `oracle_accept`, `final_score`, or `.mergeability` content cannot enter public evidence.

Acceptance criteria:
- [ ] Hidden oracle files are absent from the public worktree before the decision artifact exists.
- [ ] Reviewer packets contain public task, diff, path policy, public command output, and public receipt hashes only.
- [ ] Oracle references in reviewer packets trigger an isolation failure.

## Slice 3 -- Preserve reviewer availability and disagreement semantics

Priority: P1

Scope: Add reviewer-panel integration at the runner boundary through dependency injection. If no panel is provided, S_full is unavailable. If a panel is provided, S_full can accept or reject independently of S_probe, and the report preserves disagreement.

PRD promise: P3.

Public boundary for the first RED test: `swebench_mergeability_fixture_runner`.

Chosen seam/interface for the first RED test: reviewer panel adapter passed into the runner.

Representative action: Run one fixture with no panel and one fixture with an injected panel that disagrees with S_probe.

Allowed outcomes: Unavailable panel rows stay unavailable; injected panel decisions are recorded with packet references and decision source.

Forbidden outcomes: Missing reviewer infrastructure cannot be treated as accept, reject, oracle result, or S_probe mirror.

Acceptance criteria:
- [ ] Reviewer unavailable makes S_full unavailable with `reviewer_panel_unavailable`.
- [ ] Injected reviewer panel can disagree with S_probe and disagreement appears in rows and summaries.
- [ ] Reviewer packet references and reviewer results are attached when a panel is available.

## Slice 4 -- Prove report-only invariants and regression compatibility

Priority: P1

Scope: Add end-to-end fixture assertions and run the existing bridge and mergeability regression suites. Confirm the runner does not create policy proposals, mutate policy, or claim improvement.

PRD promise: P4.

Public boundary for the first RED test: `swebench_mergeability_fixture_runner`.

Chosen seam/interface for the first RED test: generated report artifact consumed by existing mergeability analysis.

Representative action: Run the fixture runner and inspect report flags, policy proposal outputs, and existing regression tests.

Allowed outcomes: Fixture FAR/TAR denominators are non-empty and marked report-only; existing bridge behavior remains green.

Forbidden outcomes: No policy proposal, gate advancement, default change, or improvement claim may be produced by this slice.

Acceptance criteria:
- [ ] `metric_applyable`, `improvement_claim_allowed`, `default_change_allowed`, `policy_mutated`, and `gate_advanced` are all false.
- [ ] No policy proposal or policy mutation file is created.
- [ ] Existing SWE-bench bridge and mergeability tests remain green.
