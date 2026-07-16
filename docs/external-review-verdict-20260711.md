# Verdict on the 2026-07-10 External Review — Validated, Accepted, Re-sequenced

Three independent validation agents checked every finding against the code at `HEAD≈7815dce5`. Result: **all six findings confirmed.** Two minor reviewer errors found; neither changes a verdict. This doc records what we keep, what we change, why, and the ROI-ordered plan. Goal restated: a model-agnostic harness that measurably improves externally-scored outcomes and can auto-improve.

## Validation results (mechanism-level, with quotes in agent reports)

| Review finding | Verdict | Key evidence |
|---|---|---|
| 1. No causal outcome evidence | **CONFIRMED — and understated** | Three-arm "comparison" is `execution_mode: fixture_replay, live_calls_allowed: false` — the score tie is *by construction*; wall-clock ratios 39.2x / 72.8x are real. Powered run: `status: "blocked"`, `candidate_count: 0`. Repo-wide grep: `improvement_claim_allowed: true` appears **nowhere**. |
| 2. Drift monitoring blind | **CONFIRMED** | `_extract_kind` reads only the top-level field (`rollout_watcher.py:304-308`) while real rollouts nest the type at `payload.type` — sibling modules (`run_timeline.py:98`, `telegram_progress.py:267`) prove the schema. Terminal names checked (`turn.failed`, `thread.completed`, `session.ended`, `:240-248`) can never match rollout lines. Watcher fixtures use a flattened schema real files don't have. No production writer for the run registry; workflow and monitored run-ID spaces never join. |
| 3. Semantic drift gated behind path violations | **CONFIRMED** | `if n_viol < l1_scope_violation_threshold: return` (`drift_detector.py:190-199`, default 3) strictly precedes L2/L3; no bypass. Compounding: finding 2's parsing bug means write events are recorded under wrapper kinds, so the violation counter is ~always 0 — the gate never opens on real data anyway. |
| 4. Lesson retirement reversed | **CONFIRMED** | Retirement requires `recurrence_count >= retire_after` (`state.py:839-859`) — structurally impossible for zero-recurrence lessons at any injection count. Config name `lesson_retire_after_no_benefit_injections` (no docstring) promises the opposite. Test literally named `..._no_benefit_lesson_retires` drives retirement purely by recurrence (`test_supervisor_lessons.py:116-127`). The reported 153/175 zero-recurrence-active lessons are unverifiable from source but are exactly what this code guarantees. |
| 5. Reviewers are challengers, not blinded | **CONFIRMED** | Both reviewer prompts embed the lead's full outcome ("Claude outcome JSON:", `cursor_agent.py:220-221`, `reviewer_registry.py:1565-1566`) with an explicit disprove-it framing (`agent_mailbox.py:12-19`). No blinded mode exists. Nuance: the system *does* blind oracle labels from reviewers — it blinds the answer key, not the lead's claims. |
| 6. Replay/metrics instability | **CONFIRMED** | Missing schema declarations → `status: "compatible"` (`replay_versions.py:28-30,63`). The P11 false-accept audit re-verifies "against current git state" (docstring, `quality_trends.py:167`) in the live worktree — never checks out the run's commit, so history is rewritable. Reviewer lane persisted as mutable alias `model: "default"` in committed receipts. |
| "What works" (fail-closed gate) | **CONFIRMED, with a buried gem** | 21-case constructed corpus: full gate 8/8 good accepted, 13/13 bad rejected, no improvement claim (baseline unavailable). **The review omits: the lead alone had a 30.8% false-accept rate (4/13); the panel took it to 0%.** That is the repo's only measured, attributable quality delta — component-level, fixture-based, but real. |

Reviewer errors found: `composer-2.5` appears nowhere in the repo (actual alias is `"default"`); and the 13/13 rejection belongs to the full panel, not the lead — which *strengthens* the case for the panel while weakening any claim that the lead's self-review suffices.

## Own errors this review exposed (recorded as lessons)

1. Our audits verified that code paths **connect**; this review verified that **real data flows through them**. The drift chain we called "wired within-run (conditional)" in June is mechanically wired and empirically dead — the watcher has never parsed a real terminal event. New verification standard: every "wired" claim needs one live-data trace, not just a code-path trace.
2. We flagged the L1→L2/L3 gating in the first audit as a *design description*; the reviewer correctly named it a *defect* (goal abandonment inside allowed paths is never semantically evaluated).
3. Our closeout verified lesson feedback was *called*; we never checked the retirement comparison's *direction*. Wiring ≠ semantics.

## Decisions: keep / change

**Keep (adopt as-is):**
- **Rec 2 — repair the observability layer first.** Highest ROI in the entire plan: one localized parse fix (`payload.type` descent + real terminal names) plus run-registration wiring revives drift detection, `evaluate_run`, and run linkage simultaneously — the validation found all three share that single root cause.
- **Rec 1 (partial) — freeze lesson injection now.** An unmeasured steering force with an inverted pruner and (per Eevee, [arXiv:2606.11182](https://arxiv.org/abs/2606.11182); ACE/GEPA net-negative under mixture) a known failure mode. Freeze until lesson-on/off is measured. Note: this *is* our own unshipped Eevee empty-floor item — two independent routes reached the same fix.
- **Rec 3 — decouple L2/L3 from path violations.** Trivial; rides along with the watcher fix.
- **Rec 4 — fix retirement, but by measurement, not just flipping the comparison.** No-recurrence is ambiguous (lesson helped vs. never needed) without a counterfactual. Minimum now: rename the config to match actual behavior. Real fix: holdout injection A/B; retire on benefit-ratio. Fields needed (`injection_count`, `recurrence_count`) already exist.
- **Rec 5 — pin everything.** `model: "default"` in committed evidence makes lanes unfalsifiable. Same lesson as the opus-pin and prereg SHAs; extend pins to reviewer lanes, prompts, container, CLI versions.
- **Rec 6 — blinded primary judges + outcome-aware adjudicator.** Backed by the judge-bias literature: position/verbosity/self-enhancement biases ([Zheng et al. 2023, arXiv:2306.05685](https://arxiv.org/abs/2306.05685)), self-preference ([Panickssery et al. 2024, arXiv:2404.13076](https://arxiv.org/abs/2404.13076)), unfair anchoring ([Wang et al. 2023, arXiv:2305.17926](https://arxiv.org/abs/2305.17926)). Structure it as Pocock's two-axis pattern, which we already validated: blinded Standards judge (diff+standards), blinded Spec judge (diff+spec), then the existing challenger becomes the *adjudicator* who sees the lead's claims last. The panel's measured 30.8%→0% delta says the panel seat is worth keeping; blinding makes its evidence clean.
- **Rec 7 — the preregistered paired on/off experiment.** ≥100 tasks/arm, ≥50 discordant pairs (textbook McNemar power), blinded external oracle. This is the only path to the causal answer.

**Change (two refinements, with reasons):**
- **"Freeze additional benchmark spend" → freeze *ad-hoc* spend, continue the Pro pipeline.** The SWE-bench Pro pipeline (preregistered, hash-pinned, worktree-isolated, stability-gated, Phase-0-hardened this week) is not competing spend — it is **the vehicle for Rec 7**. It already has the oracle, the corpus, the power targets (n≥30/30, ≥25 discordant), and honest-halt discipline. What it lacks is exactly what the paired report named: the single-agent baseline arm. Add supervisor-off as an arm, run the same tasks both ways, same lead model in both arms (isolating the *harness* effect — this is also what keeps the result model-agnostic), blinded oracle scoring. Building a second experiment while freezing the first would be pure waste.
- **"Freeze steering enforcement" → repair first, then decide.** Steering never fires today (finding 2); freezing a dead path changes nothing. Sequence: fix observability → observe in shadow mode → then choose enforce/advise with data.

**Reframe (honesty of the headline):** "Supervisor improves auditability and sometimes catches defects; outcome improvement unknown" — accepted as today's honest claim, with one addition the review's own citation supports: *the reviewer panel demonstrably converts a 30.8% false-accept lead into a 0% false-accept gate on constructed cases.* Unproven ≠ disproven; the machinery for proof is one baseline arm away.

## ROI-ordered plan (evidence value per unit calendar time; coding cost ignored per operator)

1. **Watcher parse fix + run registration + ID join + L2/L3 decouple** — unlocks the entire monitoring layer; everything downstream is blind until this lands.
2. **Lesson freeze + config rename** — stops unmeasured prompt steering today; zero spend.
3. **Pins + blinded-panel restructure + audit-against-recorded-baseline + replay strict mode** — makes all *future* evidence interpretable and *past* evidence immutable (audits must verify against the run's recorded commit; missing schema → `incompatible`).
4. **Pro pipeline resumes with the supervisor-off arm** — Phase 0 rerun under the hardened driver; the readout becomes the review's Rec-7 experiment. ~2–3 weeks of VM time to the honest report.
5. **Auto-improvement unfreezes behind measurement** — lessons/policy evolution return only through the empty-floor A/B (inject vs. not, replay-scored). Auto-improve gated on proven signal — which was the Eevee plan before this review independently demanded it.

## The goal, restated against this plan

Model-agnostic: the floors (git/tests/receipts) are already model-independent; blinded judges take any model; running both RCT arms with the same lead model isolates harness effect from model effect. Measurable: steps 3–4 produce the first uncontestable numbers. Auto-improving: step 5 re-opens the loop only where measurement exists — the difference between auto-improvement and auto-drift, which is the one sentence this whole program keeps re-learning.

## Sources

Validation agents' full quotes in-session (2026-07-11). [Zheng et al. 2023](https://arxiv.org/abs/2306.05685) · [Panickssery et al. 2024](https://arxiv.org/abs/2404.13076) · [Wang et al. 2023](https://arxiv.org/abs/2305.17926) · [Eevee, arXiv:2606.11182](https://arxiv.org/abs/2606.11182) · [SWE-agent ACI, arXiv:2405.15793](https://arxiv.org/abs/2405.15793) · [Lightman et al. 2023, process supervision, arXiv:2305.20050](https://arxiv.org/abs/2305.20050) · repo artifacts: `agentic-eval-live/report.json:852-905`, `powered-real-benchmark-execution-status.json`, `paired_acceptance_report.json:285-352,431-451`, `rollout_watcher.py:239-308`, `drift_detector.py:171-233`, `state.py:820-859`, `test_supervisor_lessons.py:89-195`, `cursor_agent.py:178-225`, `reviewer_registry.py:1529-1566`, `replay_versions.py:27-63`, `quality_trends.py:156-196`.
