from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from supervisor.swe_bench_mergeability import (
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
        "+new\n"
    )


def _row(
    *,
    instance_id: str,
    candidate_id: str,
    model_patch: str,
    oracle_label: str,
) -> dict:
    patch_sha = sha256(model_patch.encode("utf-8")).hexdigest()
    return {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": model_patch,
        "oracle_label": oracle_label,
        "candidate_artifact_hash": patch_sha,
        "model_patch_sha256": patch_sha,
        "diff_sha256": patch_sha,
        "origin": {
            "kind": "single_agent_solver_attempt",
            "attempt_index": 1,
        },
        "producer": {
            "runner": "fixture-single-agent",
            "model": "fixture-model",
        },
    }


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_load_predictions_accepts_good_and_bad_with_provenance(tmp_path):
    good_patch = _patch("good")
    bad_patch = _patch("bad")
    predictions_path = _write_jsonl(
        tmp_path / "pro-predictions.jsonl",
        [
            _row(
                instance_id="demo__repo-001",
                candidate_id="good-1",
                model_patch=good_patch,
                oracle_label="oracle-good",
            ),
            _row(
                instance_id="demo__repo-001",
                candidate_id="bad-1",
                model_patch=bad_patch,
                oracle_label="oracle-bad",
            ),
        ],
    )

    loaded = _load_official_predictions(predictions_path)

    rows = loaded["demo__repo-001"]
    assert [row["oracle_label"] for row in rows] == ["oracle-good", "oracle-bad"]
    assert rows[0]["candidate_artifact_hash"] == sha256(
        good_patch.encode("utf-8")
    ).hexdigest()
    assert rows[1]["model_patch_sha256"] == sha256(
        bad_patch.encode("utf-8")
    ).hexdigest()
    assert rows[0]["diff_sha256"] == rows[0]["model_patch_sha256"]
    assert rows[0]["origin"]["kind"] == "single_agent_solver_attempt"
    assert rows[0]["producer"]["model"] == "fixture-model"


def test_far_denominator_nonzero_with_oracle_bad(tmp_path):
    predictions_path = _write_jsonl(
        tmp_path / "pro-predictions.jsonl",
        [
            _row(
                instance_id="demo__repo-001",
                candidate_id="good-1",
                model_patch=_patch("good"),
                oracle_label="oracle-good",
            ),
            _row(
                instance_id="demo__repo-001",
                candidate_id="bad-1",
                model_patch=_patch("bad"),
                oracle_label="oracle-bad",
            ),
        ],
    )

    summary = summarize_swe_bench_pro_candidate_corpus(predictions_path)

    assert summary["n_good"] == 1
    assert summary["n_bad"] == 1
    assert summary["false_accept_denominator"] == 1
    assert summary["far_degenerate"] is False


def test_generator_bins_nonresolving_as_oracle_bad(tmp_path):
    attempts = [
        {
            "instance_id": "demo__repo-001",
            "candidate_id": "good-1",
            "model_patch": _patch("good"),
            "origin": {"kind": "single_agent_solver_attempt", "attempt_index": 1},
            "producer": {"runner": "fixture-single-agent", "model": "fixture-model"},
        },
        {
            "instance_id": "demo__repo-001",
            "candidate_id": "bad-1",
            "model_patch": _patch("bad"),
            "origin": {"kind": "single_agent_solver_attempt", "attempt_index": 2},
            "producer": {"runner": "fixture-single-agent", "model": "fixture-model"},
        },
        {
            "instance_id": "demo__repo-001",
            "candidate_id": "unavailable-1",
            "model_patch": _patch("unavailable"),
            "origin": {"kind": "single_agent_solver_attempt", "attempt_index": 3},
            "producer": {"runner": "fixture-single-agent", "model": "fixture-model"},
        },
        {
            "instance_id": "demo__repo-001",
            "candidate_id": "non-applying-1",
            "model_patch": _patch("nonapplying"),
            "origin": {"kind": "single_agent_solver_attempt", "attempt_index": 4},
            "producer": {"runner": "fixture-single-agent", "model": "fixture-model"},
        },
    ]
    outcomes = {
        "good-1": {
            "patch_applied": True,
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
        },
        "bad-1": {
            "patch_applied": True,
            "fail_to_pass_status": "fail",
            "pass_to_pass_status": "pass",
        },
        "unavailable-1": {
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "docker_unavailable",
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
        },
        "non-applying-1": {
            "patch_applied": False,
            "fail_to_pass_status": "fail",
            "pass_to_pass_status": "pass",
        },
    }

    report = build_swe_bench_pro_candidate_corpus(
        attempts=attempts,
        output_path=tmp_path / "pro-predictions.jsonl",
        oracle_runner=lambda attempt: outcomes[attempt["candidate_id"]],
    )

    assert report["status"] == "completed"
    assert report["summary"]["n_good"] == 1
    assert report["summary"]["n_bad"] == 1
    assert {row["candidate_id"]: row["oracle_label"] for row in report["rows"]} == {
        "good-1": "oracle-good",
        "bad-1": "oracle-bad",
    }
    assert {row["candidate_id"] for row in report["excluded"]} == {
        "unavailable-1",
        "non-applying-1",
    }

    loaded = _load_official_predictions(tmp_path / "pro-predictions.jsonl")
    assert [row["oracle_label"] for row in loaded["demo__repo-001"]] == [
        "oracle-good",
        "oracle-bad",
    ]
