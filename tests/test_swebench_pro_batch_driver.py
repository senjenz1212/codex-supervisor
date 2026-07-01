from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import swebench_pro_batch_driver as batch


def _record(instance_id: str, *, public_worktree_ref: str = "/tmp/public") -> dict:
    return {
        "instance_id": instance_id,
        "repo": "owner/repo",
        "base_commit": "abc123",
        "problem_statement": "Fix the issue.",
        "reference_patch": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n",
        "dockerhub_tag": "owner.repo-abc123",
        "FAIL_TO_PASS": ["test_file.py::test_fix"],
        "PASS_TO_PASS": ["test_file.py::test_existing"],
        "selected_test_files_to_run": ["test_file.py"],
        "before_repo_set_cmd": "echo prep",
        "public_worktree_ref": public_worktree_ref,
    }


def _solver_attempt(instance_id: str, *, candidate_id: str, patch: str) -> dict:
    return {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": patch,
        "candidate_artifact_hash": "h-" + candidate_id,
        "origin": {"kind": "solver", "solver": "claude-code-haiku"},
        "producer": {"kind": "claude_code", "model": "claude-3-5-haiku-20241022"},
    }


def _write_solver_output(path: Path, attempts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"attempts": attempts}, sort_keys=True),
        encoding="utf-8",
    )


def _write_scripts(root: Path, instance_id: str) -> None:
    instance_dir = root / instance_id
    instance_dir.mkdir(parents=True)
    (instance_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (instance_dir / "parser.py").write_text("print('ok')\n", encoding="utf-8")


def test_curate_roster_excludes_missing_scripts_and_failed_dry_oracle(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "run_scripts"
    good = "instance_good"
    bad = "instance_bad"
    missing = "instance_missing"
    _write_scripts(scripts_dir, good)
    _write_scripts(scripts_dir, bad)
    output_json = tmp_path / "output.json"
    output_json.write_text(json.dumps({"tests": ["test_file.py::test_fix"]}), encoding="utf-8")

    def fake_oracle(context):
        assert context["repo"] == "owner/repo"
        assert context["dockerhub_tag"] == "owner.repo-abc123"
        if context["instance_id"] == bad:
            return {
                "oracle_adapter_receipt": {
                    "patch_applied": True,
                    "fail_to_pass_status": "fail",
                    "pass_to_pass_status": "pass",
                    "test_command_return_code": 0,
                    "artifact_paths": {"output_json": str(output_json)},
                }
            }
        return {
            "oracle_adapter_receipt": {
                "patch_applied": True,
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
                "fail_to_pass_count": 1,
                "pass_to_pass_count": 0,
                "pass_to_pass_empty_vacuous_pass": True,
                "test_command_return_code": 0,
                "artifact_paths": {"output_json": str(output_json)},
            }
        }

    report = batch.curate_roster(
        [_record(good), _record(bad), _record(missing)],
        scripts_dir=str(scripts_dir),
        run_dry_oracle=True,
        oracle_runner=fake_oracle,
    )

    assert report["summary"] == {
        "input_instances": 3,
        "curated_instances": 1,
        "excluded_instances": 2,
        "dry_oracle_run": True,
        "vacuous_pass_to_pass_count": 1,
        "rc_nonzero_resolved_count": 0,
    }
    assert [entry["instance_id"] for entry in report["curated"]] == [good]
    assert report["curated"][0]["dry_oracle"]["fail_to_pass_count"] == 1
    assert report["curated"][0]["dry_oracle"]["pass_to_pass_count"] == 0
    assert report["curated"][0]["dry_oracle"]["pass_to_pass_empty_vacuous_pass"] is True
    reasons = {entry["instance_id"]: entry["reason"] for entry in report["excluded"]}
    assert reasons[bad].startswith("dry_oracle_gold_not_runnable")
    assert reasons[missing] == "pro_script_missing"
    assert report["metric_applyable"] is False


def test_rc_nonzero_resolved_gold_is_runnable_with_disclosure(tmp_path: Path) -> None:
    output_json = tmp_path / "output.json"
    output_json.write_text(
        json.dumps({"tests": ["test_file.py::test_fix"]}),
        encoding="utf-8",
    )
    result = {
        "oracle_adapter_receipt": {
            "patch_applied": True,
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "fail_to_pass_count": 1,
            "pass_to_pass_count": 0,
            "pass_to_pass_empty_vacuous_pass": True,
            "test_command_return_code": 7,
            "artifact_paths": {"output_json": str(output_json)},
        }
    }

    ok, reason, details = batch._oracle_gold_runnable(result)

    assert ok is True
    assert reason == ""
    assert details["test_command_return_code"] == 7
    assert details["rc_nonzero_resolved"] is True
    assert details["fail_to_pass_count"] == 1
    assert details["pass_to_pass_count"] == 0
    assert details["pass_to_pass_empty_vacuous_pass"] is True


def test_gold_dry_oracle_fails_closed_without_test_command_receipt(
    tmp_path: Path,
) -> None:
    output_json = tmp_path / "output.json"
    output_json.write_text(
        json.dumps({"tests": ["test_file.py::test_fix"]}),
        encoding="utf-8",
    )
    result = {
        "oracle_adapter_receipt": {
            "patch_applied": True,
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "artifact_paths": {"output_json": str(output_json)},
        }
    }

    ok, reason, details = batch._oracle_gold_runnable(result)

    assert ok is False
    assert reason.startswith("dry_oracle_gold_not_runnable")
    assert "test_command_return_code_present" in reason
    assert details["test_command_return_code"] is None
    assert details["rc_nonzero_resolved"] is False


@pytest.mark.parametrize(
    ("receipt_updates", "output_payload", "missing_check"),
    [
        ({"patch_applied": False}, {"tests": ["test_file.py::test_fix"]}, "patch_applied"),
        ({}, {"tests": []}, "tests_non_empty"),
        ({"fail_to_pass_status": "fail"}, {"tests": ["test_file.py::test_fix"]}, "fail_to_pass_status"),
        ({"pass_to_pass_status": "unavailable"}, {"tests": ["test_file.py::test_fix"]}, "pass_to_pass_status"),
    ],
)
def test_rc_nonzero_gold_still_fails_closed_without_resolved_status(
    tmp_path: Path,
    receipt_updates: dict,
    output_payload: dict,
    missing_check: str,
) -> None:
    output_json = tmp_path / "output.json"
    output_json.write_text(json.dumps(output_payload), encoding="utf-8")
    receipt = {
        "patch_applied": True,
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "test_command_return_code": 7,
        "artifact_paths": {"output_json": str(output_json)},
    }
    receipt.update(receipt_updates)

    ok, reason, details = batch._oracle_gold_runnable({"oracle_adapter_receipt": receipt})

    assert ok is False
    assert reason.startswith("dry_oracle_gold_not_runnable")
    assert missing_check in reason
    assert details["rc_nonzero_resolved"] is False


def test_batch_manifest_pins_thresholds_and_report_only_labels(tmp_path: Path) -> None:
    records_path = tmp_path / "records.jsonl"
    records_path.write_text(json.dumps(_record("instance_good")) + "\n", encoding="utf-8")
    config = batch.BatchConfig(
        records_path=records_path,
        output_dir=tmp_path / "out",
        powered_predictions_path=None,
        scripts_dir="/tmp/run_scripts",
        python_executable="/venv/bin/python",
        runner_command="/repo/scripts/swebench_pro_claude_code_runner.py --model claude-3-5-haiku-20241022",
        solver="claude-code-litellm-haiku-real-swebench-pro-pilot",
        model="claude-3-5-haiku-20241022",
        provider="anthropic_via_unity_litellm",
        k=9,
        max_budget_usd=1.5,
        min_good=30,
        min_bad=30,
        min_discordant=25,
        alpha=0.05,
        oracle_timeout_s=3600.0,
        run_dry_oracle=True,
        run_solver=True,
        run_labeling=True,
        run_powered=True,
        allow_live=True,
        prune_docker_between_instances=True,
        docker_prune_command="docker image prune -af",
        phase0_gate_decision_path=None,
    )

    manifest = batch._config_manifest(config, [_record("instance_good")])

    assert manifest["thresholds"] == {
        "min_good": 30,
        "min_bad": 30,
        "min_discordant": 25,
        "alpha": 0.05,
    }
    assert manifest["phases"]["prune_docker_between_instances"] is True
    assert manifest["labels"]["benchmark_oracle_kind"] == "swe_bench_held_out_test_pass_proxy"
    assert manifest["labels"]["cross_family_status"] == (
        "operator_asserted_unverified_until_attestation_exists"
    )
    assert manifest["human_mergeability_claim_allowed"] is False
    assert manifest["metric_applyable"] is False


def test_solver_spend_requires_phase0_gate_decision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SWEBENCH_PRO_ORACLE_SCRIPTS_DIR", raising=False)
    monkeypatch.delenv("SWEBENCH_PRO_ORACLE_SUBPROCESS_TIMEOUT_S", raising=False)
    records_path = tmp_path / "records.jsonl"
    instance_id = "instance_good"
    records_path.write_text(json.dumps(_record(instance_id)) + "\n", encoding="utf-8")
    scripts_dir = tmp_path / "run_scripts"
    _write_scripts(scripts_dir, instance_id)

    def fail_if_solver_runs(*_args, **_kwargs):
        raise AssertionError("solver spend must not start without Phase 0 approval")

    monkeypatch.setattr(batch, "run_solver_batch", fail_if_solver_runs)

    with pytest.raises(RuntimeError, match="Phase 0 gate decision"):
        batch.main([
            "--records",
            str(records_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--swe-bench-pro-scripts-dir",
            str(scripts_dir),
            "--allow-live",
            "--run-solver",
        ])


def test_generator_input_requires_materialized_public_worktree() -> None:
    try:
        batch._generator_input(
            {"instance_id": "instance_without_bundle", "problem_statement": "Fix."},
            model="claude-3-5-haiku-20241022",
            provider="anthropic_via_unity_litellm",
            budget=1.0,
        )
    except ValueError as exc:
        assert "public_worktree_ref" in str(exc)
    else:
        raise AssertionError("expected missing public worktree to fail closed")


def test_build_and_label_corpus_injects_oracle_context_per_candidate(tmp_path: Path) -> None:
    scripts_dir = "/opt/pro-run-scripts"
    record = _record("instance_good")
    curated = [{"instance_id": "instance_good", "record": record}]
    solver_output = tmp_path / "solver" / "instance_good" / "solver-output.json"
    _write_solver_output(
        solver_output,
        [
            _solver_attempt(
                "instance_good",
                candidate_id="instance_good-attempt-1",
                patch="diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@\n-x\n+y\n",
            ),
            _solver_attempt(
                "instance_good",
                candidate_id="instance_good-attempt-2",
                patch="diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@\n-x\n+z\n",
            ),
        ],
    )

    seen: list[dict] = []

    def fake_oracle(context):
        seen.append(dict(context))
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": {"patch_applied": True},
        }

    report = batch.build_and_label_corpus(
        curated=curated,
        solver_output_paths=[solver_output],
        output_path=tmp_path / "pro-predictions.jsonl",
        scripts_dir=scripts_dir,
        oracle_runner=fake_oracle,
    )

    assert report["status"] == "completed"
    assert len(seen) == 3  # two solver attempts plus one gold backstop
    for context in seen:
        assert context["repo"] == "owner/repo"
        assert context["dockerhub_tag"] == "owner.repo-abc123"
        assert context["base_commit"] == "abc123"
        assert context["FAIL_TO_PASS"] == ["test_file.py::test_fix"]
        assert context["PASS_TO_PASS"] == ["test_file.py::test_existing"]
        assert context["selected_test_files_to_run"] == ["test_file.py"]
        assert context["before_repo_set_cmd"] == "echo prep"
        assert context["swe_bench_pro_scripts_dir"] == scripts_dir
    gold = [ctx for ctx in seen if ctx["candidate_id"].endswith("-dataset-reference-gold")]
    assert len(gold) == 1


def test_build_and_label_corpus_fails_closed_on_empty_corpus(tmp_path: Path) -> None:
    record = _record("instance_good")
    curated = [{"instance_id": "instance_good", "record": record}]
    solver_output = tmp_path / "solver" / "instance_good" / "solver-output.json"
    _write_solver_output(
        solver_output,
        [
            _solver_attempt(
                "instance_good",
                candidate_id="instance_good-attempt-1",
                patch="diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@\n-x\n+y\n",
            ),
        ],
    )

    def unavailable_oracle(context):
        return {
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "missing_pro_oracle_context:base_commit",
        }

    predictions_path = tmp_path / "pro-predictions.jsonl"
    with pytest.raises(RuntimeError, match="no applying candidates"):
        batch.build_and_label_corpus(
            curated=curated,
            solver_output_paths=[solver_output],
            output_path=predictions_path,
            scripts_dir="/opt/pro-run-scripts",
            oracle_runner=unavailable_oracle,
        )
    assert not predictions_path.exists()


def test_build_and_label_corpus_rejects_attempt_without_curated_record(tmp_path: Path) -> None:
    solver_output = tmp_path / "solver" / "instance_x" / "solver-output.json"
    _write_solver_output(
        solver_output,
        [
            _solver_attempt(
                "unknown_instance",
                candidate_id="unknown_instance-attempt-1",
                patch="diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@\n-x\n+y\n",
            ),
        ],
    )

    with pytest.raises(RuntimeError, match="no matching curated dataset record"):
        batch.build_and_label_corpus(
            curated=[{"instance_id": "other", "record": _record("other")}],
            solver_output_paths=[solver_output],
            output_path=tmp_path / "pro-predictions.jsonl",
            scripts_dir="/opt/pro-run-scripts",
            oracle_runner=lambda ctx: {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
                "oracle_adapter_receipt": {"patch_applied": True},
            },
        )


def test_powered_stage_requires_panel_annotated_corpus(tmp_path: Path) -> None:
    predictions_path = tmp_path / "pro-predictions.jsonl"
    predictions_path.write_text(
        json.dumps({
            "instance_id": "instance_good",
            "candidate_id": "candidate-1",
            "model_patch": "diff --git a/a.txt b/a.txt\n",
            "oracle_label": "oracle_good",
            "candidate_artifact_hash": "abc123",
            "origin": {"kind": "solver"},
            "producer": {"model": "haiku"},
        })
        + "\n",
        encoding="utf-8",
    )

    try:
        batch._assert_powered_predictions_ready(predictions_path)
    except RuntimeError as exc:
        message = str(exc)
        assert "panel-annotated corpus" in message
        assert "candidate-1:reviewer_panel_results" in message
        assert "candidate-1:single_agent_baseline_decision" in message
    else:
        raise AssertionError("expected raw oracle-labeled corpus to be rejected")
