from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.swe_bench_mergeability import (
    ORACLE_BAD_LABEL,
    ORACLE_GOOD_LABEL,
    SwebenchMergeabilityFixtureRunnerError,
    _load_official_predictions,
    build_swe_bench_pro_candidate_corpus,
    summarize_swe_bench_pro_candidate_corpus,
)


def _patch(name: str) -> str:
    return (
        f"diff --git a/{name}.py b/{name}.py\n"
        f"--- a/{name}.py\n"
        f"+++ b/{name}.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        f"+{name}\n"
    )


def _attempt(
    *,
    instance_id: str,
    candidate_id: str,
    model_patch: str,
    origin_kind: str,
    producer_kind: str,
    baseline_receipt: bool = False,
) -> dict:
    patch_hash = sha256(model_patch.encode("utf-8")).hexdigest()
    attempt = {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": model_patch,
        "origin": {
            "kind": origin_kind,
            "source": "fixture",
        },
        "producer": {
            "kind": producer_kind,
            "model": "fixture-model",
            "provider": "fixture-provider",
            "runner_label": "fixture-runner",
        },
    }
    if baseline_receipt:
        attempt["single_agent_baseline_decision"] = {
            "candidate_id": candidate_id,
            "accept": False,
            "decision_source": "single_agent_candidate_generation",
            "candidate_artifact_hash": patch_hash,
            "producer": dict(attempt["producer"]),
            "prompt_sha256": "a" * 64,
        }
    return attempt


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _oracle(outcomes: dict[str, dict]):
    def _run(attempt: dict) -> dict:
        return outcomes[attempt["candidate_id"]]

    return _run


def _assert_authority_false(report: dict) -> None:
    for key in (
        "metric_applyable",
        "improvement_claim_allowed",
        "powered_improvement_claim_allowed",
        "human_mergeability_claim_allowed",
        "default_change_allowed",
        "policy_mutated",
        "gate_advanced",
    ):
        assert report[key] is False


def test_corpus_bins_good_and_bad_with_provenance(tmp_path):
    attempts = [
        _attempt(
            instance_id="instance_demo__repo-good-1",
            candidate_id="gold-1",
            model_patch=_patch("gold_one"),
            origin_kind="dataset_reference_patch",
            producer_kind="dataset",
        ),
        _attempt(
            instance_id="instance_demo__repo-good-2",
            candidate_id="gold-2",
            model_patch=_patch("gold_two"),
            origin_kind="dataset_reference_patch",
            producer_kind="dataset",
        ),
        _attempt(
            instance_id="instance_demo__repo-bad-1",
            candidate_id="solver-bad-1",
            model_patch=_patch("solver_bad"),
            origin_kind="single_agent_solver_attempt",
            producer_kind="single_agent_solver",
            baseline_receipt=True,
        ),
    ]
    report = build_swe_bench_pro_candidate_corpus(
        attempts=attempts,
        output_path=tmp_path / "pro-predictions.jsonl",
        oracle_runner=_oracle({
            "gold-1": {
                "patch_applied": True,
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
            "gold-2": {
                "patch_applied": True,
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
            "solver-bad-1": {
                "patch_applied": True,
                "fail_to_pass_status": "fail",
                "pass_to_pass_status": "pass",
            },
        }),
    )

    assert report["status"] == "completed"
    assert report["summary"]["n_good"] == 2
    assert report["summary"]["n_bad"] == 1
    assert report["summary"]["false_accept_denominator"] == 1
    assert report["summary"]["far_degenerate"] is False
    _assert_authority_false(report)

    loaded = _load_official_predictions(tmp_path / "pro-predictions.jsonl")
    rows = [row for candidates in loaded.values() for row in candidates]
    assert [row["oracle_label"] for row in rows] == [
        ORACLE_GOOD_LABEL,
        ORACLE_GOOD_LABEL,
        ORACLE_BAD_LABEL,
    ]
    assert rows[0]["origin"]["kind"] == "dataset_reference_patch"
    assert rows[0]["producer"]["kind"] == "dataset"
    assert "single_agent_baseline_decision" not in rows[0]
    assert rows[2]["origin"]["kind"] == "single_agent_solver_attempt"
    assert rows[2]["producer"]["runner_label"] == "fixture-runner"
    assert rows[2]["single_agent_baseline_decision"]["candidate_id"] == "solver-bad-1"
    for row in rows:
        patch_hash = sha256(row["model_patch"].encode("utf-8")).hexdigest()
        assert row["candidate_artifact_hash"] == patch_hash
        assert row["model_patch_sha256"] == patch_hash
        assert row["diff_sha256"] == patch_hash
        assert row["origin"]
        assert row["producer"]


def test_n_bad_nonzero_makes_far_real(tmp_path):
    rows = []
    for label, name in (
        (ORACLE_GOOD_LABEL, "good"),
        (ORACLE_BAD_LABEL, "bad"),
    ):
        model_patch = _patch(name)
        patch_hash = sha256(model_patch.encode("utf-8")).hexdigest()
        rows.append({
            "instance_id": f"instance_demo__repo-{name}",
            "candidate_id": name,
            "model_patch": model_patch,
            "oracle_label": label,
            "patch_applied": True,
            "candidate_artifact_hash": patch_hash,
            "model_patch_sha256": patch_hash,
            "diff_sha256": patch_hash,
            "origin": {"kind": "fixture"},
            "producer": {"kind": "fixture"},
        })

    summary = summarize_swe_bench_pro_candidate_corpus(
        _write_jsonl(tmp_path / "pro-predictions.jsonl", rows)
    )

    assert summary["n_bad"] == 1
    assert summary["false_accept_denominator"] == 1
    assert summary["far_degenerate"] is False


def test_nonresolving_only_counts_when_patch_applied(tmp_path):
    attempts = [
        _attempt(
            instance_id="instance_demo__repo-applies",
            candidate_id="applies-but-fails",
            model_patch=_patch("applies"),
            origin_kind="single_agent_solver_attempt",
            producer_kind="single_agent_solver",
        ),
        _attempt(
            instance_id="instance_demo__repo-nonapplying",
            candidate_id="nonapplying",
            model_patch=_patch("nonapplying"),
            origin_kind="single_agent_solver_attempt",
            producer_kind="single_agent_solver",
        ),
    ]

    report = build_swe_bench_pro_candidate_corpus(
        attempts=attempts,
        output_path=tmp_path / "pro-predictions.jsonl",
        oracle_runner=_oracle({
            "applies-but-fails": {
                "patch_applied": True,
                "fail_to_pass_status": "fail",
                "pass_to_pass_status": "pass",
            },
            "nonapplying": {
                "patch_applied": False,
                "fail_to_pass_status": "fail",
                "pass_to_pass_status": "pass",
            },
        }),
    )

    assert report["summary"]["n_bad"] == 1
    assert [row["candidate_id"] for row in report["rows"]] == ["applies-but-fails"]
    assert report["rows"][0]["oracle_label"] == ORACLE_BAD_LABEL
    assert {row["candidate_id"] for row in report["excluded"]} == {"nonapplying"}


def test_gold_only_corpus_is_completed_but_far_degenerate(tmp_path):
    attempts = [
        _attempt(
            instance_id="instance_demo__repo-gold-1",
            candidate_id="gold-1",
            model_patch=_patch("gold_one"),
            origin_kind="dataset_reference_patch",
            producer_kind="dataset",
        ),
        _attempt(
            instance_id="instance_demo__repo-gold-2",
            candidate_id="gold-2",
            model_patch=_patch("gold_two"),
            origin_kind="dataset_reference_patch",
            producer_kind="dataset",
        ),
    ]

    report = build_swe_bench_pro_candidate_corpus(
        attempts=attempts,
        output_path=tmp_path / "pro-predictions.jsonl",
        oracle_runner=_oracle({
            "gold-1": {
                "patch_applied": True,
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
            "gold-2": {
                "patch_applied": True,
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
        }),
    )

    assert report["status"] == "completed"
    assert report["summary"]["n_good"] == 2
    assert report["summary"]["n_bad"] == 0
    assert report["summary"]["false_accept_denominator"] == 0
    assert report["summary"]["far_degenerate"] is True
    _assert_authority_false(report)


def test_corpus_rejects_rows_without_provenance(tmp_path):
    missing_origin = _attempt(
        instance_id="instance_demo__repo-missing",
        candidate_id="missing-origin",
        model_patch=_patch("missing_origin"),
        origin_kind="single_agent_solver_attempt",
        producer_kind="single_agent_solver",
    )
    missing_origin.pop("origin")

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="origin"):
        build_swe_bench_pro_candidate_corpus(
            attempts=[missing_origin],
            output_path=tmp_path / "pro-predictions.jsonl",
            oracle_runner=_oracle({
                "missing-origin": {
                    "patch_applied": True,
                    "fail_to_pass_status": "pass",
                    "pass_to_pass_status": "pass",
                },
            }),
        )
