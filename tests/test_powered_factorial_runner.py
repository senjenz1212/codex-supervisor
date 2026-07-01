from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.swe_bench_mergeability import (
    SwebenchMergeabilityFixtureRunnerError,
    swebench_mergeability_powered_factorial_runner,
)
from supervisor.swe_bench_mergeability_cli import main as swebench_cli_main


def _receipt(
    *,
    candidate_id: str,
    patch_hash: str,
    accept: bool,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "accept": accept,
        "candidate_artifact_hash": patch_hash,
        "decision_source": "single_agent_candidate_generation",
        "producer": {
            "provider": "fixture-provider",
            "model": "fixture-single-agent",
            "runner_label": "fixture-powered-runner",
        },
        "prompt_sha256": sha256(f"prompt:{candidate_id}".encode("utf-8")).hexdigest(),
    }


def _decision(accept: bool, *, source: str) -> dict[str, object]:
    return {
        "accept": accept,
        "unavailable": False,
        "decision_source": source,
    }


def _write_pro_predictions(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    specs = [
        ("instance-alpha", "alpha-good", "oracle-good", True, True),
        ("instance-alpha", "alpha-bad", "oracle-bad", True, False),
        ("instance-beta", "beta-good", "oracle-good", False, False),
        ("instance-beta", "beta-bad", "oracle-bad", False, False),
    ]
    for instance_id, candidate_id, oracle_label, baseline_accept, full_accept in specs:
        patch = f"diff --git a/{candidate_id}.py b/{candidate_id}.py\n+{candidate_id}\n"
        patch_hash = sha256(patch.encode("utf-8")).hexdigest()
        rows.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "model_patch": patch,
            "oracle_label": oracle_label,
            "patch_applied": True,
            "candidate_artifact_hash": patch_hash,
            "model_patch_sha256": patch_hash,
            "diff_sha256": patch_hash,
            "origin": {"kind": "solver_attempt", "attempt": candidate_id},
            "producer": {
                "provider": "fixture-provider",
                "model": "fixture-model",
                "runner_label": "fixture-runner",
            },
            "single_agent_baseline_decision": _receipt(
                candidate_id=candidate_id,
                patch_hash=patch_hash,
                accept=baseline_accept,
            ),
            "same_model_multi_agent_decision": _decision(
                full_accept,
                source="same_model_multi_agent_fixture",
            ),
            "hetero_multi_reviewer_decision": _decision(
                full_accept,
                source="hetero_multi_reviewer_fixture",
            ),
            "runtime_evidence_floor_decision": _decision(
                full_accept,
                source="runtime_evidence_floor_fixture",
            ),
            "full_supervisor_stack_decision": _decision(
                full_accept,
                source="full_supervisor_stack_fixture",
            ),
            "reviewer_panel_results": [
                {
                    "reviewer_id": "fixture-cross-family-reviewer",
                    "decision": "accept" if full_accept else "deny",
                    "available": True,
                }
            ],
        })
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return rows


def _rewrite_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cli_exposes_power_thresholds(tmp_path, capsys):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    _write_pro_predictions(predictions_path)
    output_dir = tmp_path / "powered"

    exit_code = swebench_cli_main([
        "--powered-factorial",
        "--predictions",
        str(predictions_path),
        "--output-dir",
        str(output_dir),
        "--min-good",
        "1",
        "--min-bad",
        "1",
        "--min-discordant",
        "1",
        "--alpha",
        "1.0",
    ])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "reported"
    assert summary["report"] == str(output_dir / "powered_factorial_report.json")
    assert summary["evidence_conversion_power_contract"]["status"] == "qualified"
    assert summary["evidence_conversion_power_contract"]["thresholds"] == {
        "min_good": 1,
        "min_bad": 1,
        "min_discordant": 1,
        "alpha": 1.0,
    }


def test_factorial_runner_consumes_pro_corpus(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    rows = _write_pro_predictions(predictions_path)

    report = swebench_mergeability_powered_factorial_runner(
        predictions_path=predictions_path,
        output_dir=tmp_path / "powered",
        min_good=1,
        min_bad=1,
        min_discordant=1,
        alpha=1.0,
    )

    expected_hashes = {
        str(row["candidate_id"]): str(row["candidate_artifact_hash"])
        for row in rows
    }
    assert report["source_predictions_path"] == str(predictions_path.resolve())
    assert report["pro_candidate_count"] == len(rows)
    assert report["candidate_count"] == len(rows)
    assert {row["candidate_id"]: row["candidate_hash"] for row in report["per_task_results"]} == expected_hashes
    assert report["arms"]["single_agent_baseline"]["availability_status"] == "available"
    assert (tmp_path / "powered" / "powered_factorial_report.json").exists()


def test_powered_runner_reports_source_disclosure_counts(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    rows = _write_pro_predictions(predictions_path)
    rows[0]["pass_to_pass_empty_vacuous_pass"] = True
    rows[1]["rc_nonzero_resolved"] = True
    _rewrite_rows(predictions_path, rows)

    report = swebench_mergeability_powered_factorial_runner(
        predictions_path=predictions_path,
        output_dir=tmp_path / "powered",
        min_good=1,
        min_bad=1,
        min_discordant=1,
        alpha=1.0,
    )

    assert report["source_disclosure_counts"] == {
        "vacuous_pass_to_pass_count": 1,
        "rc_nonzero_resolved_count": 1,
    }


def test_power_contract_qualified_only_when_powered(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    _write_pro_predictions(predictions_path)

    report = swebench_mergeability_powered_factorial_runner(
        predictions_path=predictions_path,
        output_dir=tmp_path / "powered",
        min_good=1,
        min_bad=1,
        min_discordant=1,
        alpha=1.0,
    )

    contract = report["evidence_conversion_power_contract"]
    assert contract["status"] == "qualified"
    assert contract["sample_size_status"] == "sufficient"
    assert contract["paired_power_status"] == "sufficient"
    assert contract["required_comparison"]["mcnemar_test_passed"] is True
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


def test_underpowered_reports_not_qualified(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    _write_pro_predictions(predictions_path)

    report = swebench_mergeability_powered_factorial_runner(
        predictions_path=predictions_path,
        output_dir=tmp_path / "powered",
        min_good=10,
        min_bad=10,
        min_discordant=25,
        alpha=0.05,
    )

    contract = report["evidence_conversion_power_contract"]
    assert contract["status"] == "underpowered"
    assert "sample_size_underpowered" in contract["reasons"]
    assert "paired_power_underpowered" in contract["reasons"]
    assert contract["thresholds"] == {
        "min_good": 10,
        "min_bad": 10,
        "min_discordant": 25,
        "alpha": 0.05,
    }


def test_powered_runner_rejects_missing_arm_decision(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    rows = _write_pro_predictions(predictions_path)
    rows[0].pop("full_supervisor_stack_decision")
    _rewrite_rows(predictions_path, rows)

    with pytest.raises(
        SwebenchMergeabilityFixtureRunnerError,
        match="missing full_supervisor_stack_decision",
    ):
        swebench_mergeability_powered_factorial_runner(
            predictions_path=predictions_path,
            output_dir=tmp_path / "powered",
            min_good=1,
            min_bad=1,
            min_discordant=1,
            alpha=1.0,
        )


def test_powered_runner_rejects_hash_mismatched_baseline_receipt(tmp_path):
    predictions_path = tmp_path / "pro-predictions.jsonl"
    rows = _write_pro_predictions(predictions_path)
    baseline = rows[0]["single_agent_baseline_decision"]
    assert isinstance(baseline, dict)
    baseline["candidate_artifact_hash"] = "0" * 64
    _rewrite_rows(predictions_path, rows)

    with pytest.raises(
        SwebenchMergeabilityFixtureRunnerError,
        match="baseline receipt is not trusted: candidate_artifact_hash_mismatch",
    ):
        swebench_mergeability_powered_factorial_runner(
            predictions_path=predictions_path,
            output_dir=tmp_path / "powered",
            min_good=1,
            min_bad=1,
            min_discordant=1,
            alpha=1.0,
        )
