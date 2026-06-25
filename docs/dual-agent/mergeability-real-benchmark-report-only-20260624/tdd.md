# TDD Plan

## Issue 1: Real-Benchmark Evidence Fields

RED 1: Public-boundary test calls `swebench_mergeability_official_all_arms_diagnostic_runner` and asserts the report includes oracle limitation, no maintainer-mergeability claim, freeze-before-oracle proof, false-accept reduction at matched true-accept, S_probe-vs-S_full, reviewer marginal, and report-only flags.

GREEN 1: Add additive fields to `_build_official_all_arms_diagnostic_report` and fix bridge panel marginal availability for populated S_full rows.

## Issue 2: Reviewer Evidence Fields

RED 1: Public-boundary test supplies reviewer labels and asserts rubric coverage and abstention coverage are reported with deterministic oracle scoring authority.

GREEN 1: Preserve rubric fields in public reviewer summaries and adapt official replay rows through existing mergeability reviewer evidence helpers.

RED 2: Public-boundary test supplies OpenAI-family generator receipts and a single OpenAI-family reviewer and asserts self-preference warnings are emitted.

GREEN 2: Feed baseline producer family into the existing generator-disjointness helper.

## TDD Grill Findings

Resolved: The first RED test starts at the public official all-arms runner.

Resolved: Hidden oracle material remains behind the existing official replay leak scanner.

Resolved: All fields are additive and do not change policy authority.
