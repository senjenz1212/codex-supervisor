from __future__ import annotations

import json
import subprocess

from supervisor.probe_cohorts import summarize_probe_cohort


def test_probe_cohort_marks_repeated_expected_blocks_stable():
    summary = summarize_probe_cohort(
        cohort_id="cohort-1",
        trial_summaries=[
            _trial("trial-1", status="blocked_as_expected", mast_code="FM-3.2", cost=0.2, tokens_in=100),
            _trial("trial-2", status="blocked_as_expected", mast_code="FM-3.2", cost=0.3, tokens_in=120),
            _trial("trial-3", status="blocked_as_expected", mast_code="FM-3.2", cost=0.4, tokens_in=140),
        ],
    )

    assert summary["classification"] == "STABLE"
    assert summary["trial_count"] == 3
    assert summary["status_counts"] == {"blocked_as_expected": 3}
    assert summary["failure_counts_by_mast_code"] == {"FM-3.2": 3}
    assert summary["totals"]["cost_usd"] == 0.9
    assert summary["totals"]["tokens_in"] == 360
    assert summary["worst_case_observed"]["tokens_in"] == 140
    assert summary["trials"][0]["artifact_dir"] == "docs/dual-agent/trial-1"


def test_probe_cohort_marks_single_outlier_as_drift_one():
    summary = summarize_probe_cohort(
        cohort_id="cohort-1",
        trial_summaries=[
            _trial("trial-1", status="blocked_as_expected", mast_code="FM-3.2"),
            _trial("trial-2", status="blocked_as_expected", mast_code="FM-3.2"),
            _trial("trial-3", status="unexpected", mast_code="FM-3.3"),
        ],
    )

    assert summary["classification"] == "DRIFT-1"
    assert summary["unexpected_count"] == 1
    assert summary["failure_counts_by_mast_code"] == {"FM-3.2": 2, "FM-3.3": 1}


def test_probe_cohort_marks_no_majority_as_flipping():
    summary = summarize_probe_cohort(
        cohort_id="cohort-1",
        trial_summaries=[
            _trial("trial-1", status="blocked_as_expected", mast_code="FM-3.2"),
            _trial("trial-2", status="unexpected", mast_code="FM-3.3"),
            _trial("trial-3", status="accepted", mast_code=None),
        ],
    )

    assert summary["classification"] == "FLIPPING"


def test_live_failure_mode_cohort_wrapper_writes_artifacts_with_trial_identity(tmp_path, monkeypatch):
    import scripts.probe_live_failure_mode_cohort as cohort_script

    calls: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text):
        calls.append(list(command))
        output_index = command.index("--output-dir") + 1
        output_dir = (cwd / command[output_index]).resolve()
        task_id = command[command.index("--task-id") + 1]
        run_id = command[command.index("--run-id") + 1]
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(json.dumps(_trial(
            task_id,
            status="blocked_as_expected",
            mast_code="FM-3.2",
        ) | {"task_id": task_id, "run_id": run_id}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(cohort_script.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    rc = cohort_script.main_with_args([
        "--cohort-id",
        "cohort-test",
        "--trials",
        "2",
        "--output-root",
        str(tmp_path / "docs" / "dual-agent"),
        "--fixture-root",
        str(tmp_path / "fixtures"),
        "--skip-cursor",
    ])

    assert rc == 0
    assert calls[0][calls[0].index("--task-id") + 1] == "cohort-test-trial-01"
    assert calls[1][calls[1].index("--run-id") + 1] == "cohort-test-trial-02"
    cohort_summary = json.loads(
        (tmp_path / "docs" / "dual-agent" / "cohort-test" / "cohort-summary.json").read_text()
    )
    assert [trial["trial_id"] for trial in cohort_summary["trials"]] == [
        "cohort-test-trial-01",
        "cohort-test-trial-02",
    ]
    proposals = json.loads(
        (tmp_path / "docs" / "dual-agent" / "cohort-test" / "stability-proposals.json").read_text()
    )
    assert proposals["proposals"][0]["status"] == "proposed"


def _trial(
    trial_id: str,
    *,
    status: str,
    mast_code: str | None,
    cost: float = 0.0,
    tokens_in: int = 0,
) -> dict:
    return {
        "trial_id": trial_id,
        "status": status,
        "task_id": trial_id,
        "run_id": trial_id,
        "artifact_export": {"output_dir": f"docs/dual-agent/{trial_id}"},
        "fixture_dir": f"tests/fixtures/dual_agent/{trial_id}",
        "failure_taxonomy": {"mast_code": mast_code} if mast_code else None,
        "claim_without_receipts": {"status": "red" if mast_code else "green"},
        "claude": {"cost_usd": cost, "tokens_in": tokens_in, "tokens_out": 20},
        "cursor": {"accepted": True},
    }
