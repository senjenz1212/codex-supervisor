from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts import swebench_pro_label_stability as stability


def _record(instance_id: str) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "repo": "owner/repo",
        "base_commit": "abc123",
        "dockerhub_tag": "owner.repo-abc123",
        "reference_patch": "diff --git a/a.txt b/a.txt\n",
        "FAIL_TO_PASS": ["tests/test_fix.py::test_fix"],
        "PASS_TO_PASS": ["tests/test_fix.py::test_existing"],
        "selected_test_files_to_run": ["tests/test_fix.py"],
        "before_repo_set_cmd": "echo prep",
    }


def _prediction(
    instance_id: str,
    candidate_id: str,
    *,
    oracle_label: str = "oracle-good",
    patch: str | None = None,
) -> dict[str, Any]:
    model_patch = patch or f"diff --git a/{candidate_id}.txt b/{candidate_id}.txt\n"
    patch_hash = sha256(model_patch.encode("utf-8")).hexdigest()
    return {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": model_patch,
        "oracle_label": oracle_label,
        "patch_applied": True,
        "candidate_artifact_hash": patch_hash,
        "model_patch_sha256": patch_hash,
        "diff_sha256": patch_hash,
        "origin": {"kind": "single_agent_solver_attempt"},
        "producer": {"model": "claude-3-5-haiku-20241022"},
    }


class FakeOracle:
    def __init__(self, outcomes_by_candidate: Mapping[str, list[dict[str, Any]]]) -> None:
        self.outcomes_by_candidate = {
            key: list(value)
            for key, value in outcomes_by_candidate.items()
        }
        self.contexts: list[dict[str, Any]] = []

    def __call__(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        captured = dict(context)
        self.contexts.append(captured)
        candidate_id = str(captured["candidate_id"])
        outcomes = self.outcomes_by_candidate[candidate_id]
        index = len([
            item
            for item in self.contexts
            if item["candidate_id"] == candidate_id
        ]) - 1
        return outcomes[index]


def _pass() -> dict[str, Any]:
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": {"patch_applied": True},
    }


def _fail() -> dict[str, Any]:
    return {
        "fail_to_pass_status": "fail",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": {"patch_applied": True},
    }


def _pass_with_patch_applied(raw: Any) -> dict[str, Any]:
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": {"patch_applied": raw},
    }


def _unavailable(reason: str = "official_oracle_timeout") -> dict[str, Any]:
    return {
        "oracle_unavailable": True,
        "oracle_unavailable_reason": reason,
        "fail_to_pass_status": "unavailable",
        "pass_to_pass_status": "unavailable",
    }


def test_stable_candidate_is_kept_with_original_label_and_context() -> None:
    prediction = _prediction("instance-a", "candidate-a", oracle_label="oracle-good")
    fake_oracle = FakeOracle({"candidate-a": [_pass(), _pass(), _pass()]})

    repeated = stability.repeat_oracle_labels(
        [prediction],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )
    stable_predictions, dropped, flake_rate = stability.filter_stable_corpus(
        [prediction],
        repeated,
    )

    assert stable_predictions == [prediction]
    assert dropped == []
    assert flake_rate == 0.0
    assert stability.classify_stability(
        repeated[stability.candidate_key(prediction)]["runs"]
    )["status"] == "STABLE"
    assert len(fake_oracle.contexts) == 3
    for context in fake_oracle.contexts:
        assert context["repo"] == "owner/repo"
        assert context["dockerhub_tag"] == "owner.repo-abc123"
        assert context["base_commit"] == "abc123"
        assert context["FAIL_TO_PASS"] == ["tests/test_fix.py::test_fix"]
        assert context["PASS_TO_PASS"] == ["tests/test_fix.py::test_existing"]
        assert context["selected_test_files_to_run"] == ["tests/test_fix.py"]
        assert context["before_repo_set_cmd"] == "echo prep"
        assert context["swe_bench_pro_scripts_dir"] == "/opt/pro-run-scripts"


def test_disagreeing_completed_repeats_are_dropped_as_unstable() -> None:
    stable = _prediction("instance-a", "candidate-stable")
    flaky = _prediction("instance-a", "candidate-flaky", oracle_label="oracle-bad")
    fake_oracle = FakeOracle({
        "candidate-stable": [_pass(), _pass(), _pass()],
        "candidate-flaky": [_fail(), _pass(), _fail()],
    })

    repeated = stability.repeat_oracle_labels(
        [stable, flaky],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )
    stable_predictions, dropped, flake_rate = stability.filter_stable_corpus(
        [stable, flaky],
        repeated,
    )

    assert stable_predictions == [stable]
    assert dropped == [{
        "instance_id": "instance-a",
        "candidate_id": "candidate-flaky",
        "candidate_artifact_hash": flaky["candidate_artifact_hash"],
        "reason": "unstable_label",
    }]
    assert flake_rate == 0.5


def test_any_unavailable_repeat_is_dropped_fail_closed_not_flaky() -> None:
    stable = _prediction("instance-a", "candidate-stable")
    unavailable = _prediction("instance-a", "candidate-unavailable")
    fake_oracle = FakeOracle({
        "candidate-stable": [_pass(), _pass(), _pass()],
        "candidate-unavailable": [_pass(), _unavailable("docker_timeout"), _pass()],
    })

    repeated = stability.repeat_oracle_labels(
        [stable, unavailable],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )
    stable_predictions, dropped, flake_rate = stability.filter_stable_corpus(
        [stable, unavailable],
        repeated,
    )

    assert stable_predictions == [stable]
    assert dropped == [{
        "instance_id": "instance-a",
        "candidate_id": "candidate-unavailable",
        "candidate_artifact_hash": unavailable["candidate_artifact_hash"],
        "reason": "oracle_unavailable:docker_timeout",
    }]
    assert flake_rate == 0.0


def test_missing_or_string_false_patch_apply_evidence_is_unavailable() -> None:
    missing_patch_evidence = [
        {"fail_to_pass_status": "pass", "pass_to_pass_status": "pass"},
        {"fail_to_pass_status": "pass", "pass_to_pass_status": "pass"},
        {"fail_to_pass_status": "pass", "pass_to_pass_status": "pass"},
    ]
    missing = stability.classify_stability([
        stability._oracle_run_summary(raw, repeat_index=index, expected_repeats=3)
        for index, raw in enumerate(missing_patch_evidence, start=1)
    ])
    string_false = stability.classify_stability([
        stability._oracle_run_summary(
            _pass_with_patch_applied("false"),
            repeat_index=index,
            expected_repeats=3,
        )
        for index in range(1, 4)
    ])

    assert missing == {
        "status": "UNAVAILABLE",
        "reason": "oracle_unavailable:patch_applied_evidence_missing_or_false",
    }
    assert string_false == {
        "status": "UNAVAILABLE",
        "reason": "oracle_unavailable:patch_apply_failed",
    }


def test_public_classifier_requires_expected_repeat_count() -> None:
    assert stability.classify_stability([
        {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_unavailable": False,
        }
    ]) == {
        "status": "UNAVAILABLE",
        "reason": "missing_expected_repeats",
    }


def test_flake_rate_uses_unstable_over_total_evaluated() -> None:
    rows = [
        _prediction("instance-a", "candidate-stable-1"),
        _prediction("instance-a", "candidate-stable-2"),
        _prediction("instance-a", "candidate-flaky"),
        _prediction("instance-a", "candidate-unavailable"),
    ]
    fake_oracle = FakeOracle({
        "candidate-stable-1": [_pass(), _pass(), _pass()],
        "candidate-stable-2": [_fail(), _fail(), _fail()],
        "candidate-flaky": [_fail(), _pass(), _fail()],
        "candidate-unavailable": [_pass(), _unavailable("parser_empty"), _pass()],
    })

    repeated = stability.repeat_oracle_labels(
        rows,
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )
    stable_predictions, dropped, flake_rate = stability.filter_stable_corpus(
        rows,
        repeated,
    )

    assert [row["candidate_id"] for row in stable_predictions] == [
        "candidate-stable-1",
        "candidate-stable-2",
    ]
    assert [row["reason"] for row in dropped] == [
        "unstable_label",
        "oracle_unavailable:parser_empty",
    ]
    assert flake_rate == 0.25


def test_kept_candidates_are_not_relabelled_or_mutated() -> None:
    row = _prediction("instance-a", "candidate-good", oracle_label="oracle-bad")
    row["single_agent_baseline_decision"] = {
        "accept": False,
        "candidate_artifact_hash": row["candidate_artifact_hash"],
    }
    original = dict(row)
    fake_oracle = FakeOracle({"candidate-good": [_pass(), _pass(), _pass()]})

    repeated = stability.repeat_oracle_labels(
        [row],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )
    stable_predictions, _, _ = stability.filter_stable_corpus([row], repeated)

    assert stable_predictions == [original]
    assert row == original
    assert stable_predictions[0]["oracle_label"] == "oracle-bad"
    assert stable_predictions[0]["candidate_artifact_hash"] == original["candidate_artifact_hash"]
    assert stable_predictions[0]["model_patch_sha256"] == original["model_patch_sha256"]
    assert stable_predictions[0]["diff_sha256"] == original["diff_sha256"]


def test_secret_bearing_prediction_fails_closed_before_stable_output() -> None:
    row = _prediction("instance-a", "candidate-secret")
    row["api_key"] = "sk-ant-secret-value-that-must-not-appear"
    fake_oracle = FakeOracle({"candidate-secret": [_pass(), _pass(), _pass()]})
    repeated = stability.repeat_oracle_labels(
        [row],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )

    with pytest.raises(RuntimeError, match="secret-like value"):
        stability.filter_stable_corpus([row], repeated)


def test_empty_input_or_all_dropped_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="empty predictions"):
        stability.filter_stable_corpus([], {})

    flaky = _prediction("instance-a", "candidate-flaky")
    fake_oracle = FakeOracle({"candidate-flaky": [_fail(), _pass(), _fail()]})
    repeated = stability.repeat_oracle_labels(
        [flaky],
        repeats=3,
        oracle_runner=fake_oracle,
        records_by_instance={"instance-a": _record("instance-a")},
        scripts_dir="/opt/pro-run-scripts",
    )

    with pytest.raises(RuntimeError, match="all candidates were dropped"):
        stability.filter_stable_corpus([flaky], repeated)


def test_cli_without_allow_live_refuses_before_oracle_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = tmp_path / "records.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    output = tmp_path / "stable.jsonl"
    report = tmp_path / "report.json"
    records.write_text(json.dumps(_record("instance-a")) + "\n", encoding="utf-8")
    predictions.write_text(
        json.dumps(_prediction("instance-a", "candidate-a")) + "\n",
        encoding="utf-8",
    )
    calls = {"count": 0}

    def forbidden_oracle(context: Mapping[str, Any]) -> Mapping[str, Any]:
        calls["count"] += 1
        raise AssertionError("CLI called live oracle without --allow-live")

    monkeypatch.setattr(stability, "run_swe_bench_pro_oracle", forbidden_oracle)

    exit_code = stability.main([
        "--records",
        str(records),
        "--predictions",
        str(predictions),
        "--swe-bench-pro-scripts-dir",
        "/opt/pro-run-scripts",
        "--output",
        str(output),
        "--report",
        str(report),
    ])

    assert exit_code == 2
    assert calls["count"] == 0
    assert not output.exists()
    assert not report.exists()


def test_secret_bearing_oracle_reason_is_sanitized_in_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-ant-secret-value-that-must-not-appear"
    records = tmp_path / "records.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    output = tmp_path / "stable.jsonl"
    report = tmp_path / "report.json"
    records.write_text(json.dumps(_record("instance-a")) + "\n", encoding="utf-8")
    predictions.write_text(
        json.dumps(_prediction("instance-a", "candidate-a")) + "\n",
        encoding="utf-8",
    )
    fake_oracle = FakeOracle({
        "candidate-a": [
            _unavailable(f"docker stderr included {secret}"),
            _unavailable(f"docker stderr included {secret}"),
            _unavailable(f"docker stderr included {secret}"),
        ]
    })
    monkeypatch.setattr(stability, "run_swe_bench_pro_oracle", fake_oracle)

    exit_code = stability.main([
        "--records",
        str(records),
        "--predictions",
        str(predictions),
        "--swe-bench-pro-scripts-dir",
        "/opt/pro-run-scripts",
        "--output",
        str(output),
        "--report",
        str(report),
        "--allow-live",
    ])

    assert exit_code == 1
    assert not output.exists()
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert secret not in report_text
    assert "oracle_unavailable:redacted_oracle_unavailable_reason" in report_text


def test_outputs_do_not_include_secret_env_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-ant-secret-value-that-must-not-appear"
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", secret)
    records = tmp_path / "records.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    output = tmp_path / "stable.jsonl"
    report = tmp_path / "report.json"
    records.write_text(json.dumps(_record("instance-a")) + "\n", encoding="utf-8")
    predictions.write_text(
        json.dumps(_prediction("instance-a", "candidate-a")) + "\n",
        encoding="utf-8",
    )
    fake_oracle = FakeOracle({"candidate-a": [_pass(), _pass(), _pass()]})
    monkeypatch.setattr(stability, "run_swe_bench_pro_oracle", fake_oracle)

    exit_code = stability.main([
        "--records",
        str(records),
        "--predictions",
        str(predictions),
        "--swe-bench-pro-scripts-dir",
        "/opt/pro-run-scripts",
        "--repeats",
        "3",
        "--output",
        str(output),
        "--report",
        str(report),
        "--allow-live",
    ])

    assert exit_code == 0
    assert secret not in output.read_text(encoding="utf-8")
    assert secret not in report.read_text(encoding="utf-8")
    payload = json.loads(report.read_text(encoding="utf-8"))
    for flag in stability.AUTHORITY_FLAGS:
        assert payload[flag] is False
    assert payload["labels"]["report_only"] is True
