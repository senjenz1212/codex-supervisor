# Tri-Agent Findings

Task id: `repo-commit-hygiene-20260626`

## Validator A: no-mistakes self-contained change

Verdict: PASS

Evidence:

- `supervisor/no_mistakes.py` adds workflow-artifact stash preflight, missing-outcome blocking, `step:` gate parsing, and checked-out-branch validation behavior.
- `tests/test_no_mistakes.py` covers TOON findings with the action column before description, missing outcome blocking, artifact stash/restore, and checked-out-branch validation.
- Validator A ran `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_no_mistakes.py -p no:cacheprovider` and observed `17 passed`.

Resolution:

- Stage and commit only `supervisor/no_mistakes.py` and `tests/test_no_mistakes.py` for the source/test commit.

## Validator B: repo hygiene

Verdict: FAIL before remediation

Evidence:

- `.gitignore` did not previously ignore all of `.scratch/`; it only ignored `.scratch/gui-probes/`.
- Adding `.scratch/` ignores new scratch files, but `git ls-files .scratch | wc -l` reported 85 already tracked scratch paths.
- The broader worktree also contained uncommitted source, tests, `uv.lock`, packet docs, and untracked docs.

Resolution:

- Commit `.gitignore` with `.scratch/`.
- Remove already tracked scratch paths from the index with `git rm --cached`, preserving local files on disk.
- Do not stage scratch content.

## Validator C: leakage and bundling risk

Verdict: FAIL for bundling as-is

Evidence:

- Safe no-mistakes commit boundary is `supervisor/no_mistakes.py` plus `tests/test_no_mistakes.py`; `.gitignore` belongs to the hygiene commit.
- `uv.lock` is suspicious for this source change because the no-mistakes diff uses only standard-library changes.
- Existing dirty transcript/replay packet docs and unrelated untracked docs are unsafe to bundle with the no-mistakes source commit.
- Scratch includes token-like local runtime state and must not be force-added.

Resolution:

- Keep the no-mistakes source/test commit separate.
- Commit scratch tracking cleanup separately.
- Commit the current task packet separately.
- Preserve unrelated dirty docs and lockfile state outside these commits instead of bundling them into the safety-gate commit.

## Accepted Ledger Records

```jsonl
{"stage":"prd_review","task_id":"repo-commit-hygiene-20260626","status":"accepted","evidence":["docs/dual-agent/repo-commit-hygiene-20260626/prd.md","docs/dual-agent/repo-commit-hygiene-20260626/grill-findings.md"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
{"stage":"tri_agent_review","task_id":"repo-commit-hygiene-20260626","status":"accepted_with_remediation","evidence":["validator_a_pass","validator_b_tracked_scratch_gap","validator_c_bundling_gap"],"authority_flags":{"metric_applyable":false,"improvement_claim_allowed":false,"powered_improvement_claim_allowed":false,"human_mergeability_claim_allowed":false,"default_change_allowed":false,"policy_mutated":false,"gate_advanced":false}}
```
