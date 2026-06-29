from __future__ import annotations

import json
from pathlib import Path

from scripts import swebench_pro_batch_driver as batch


def _record(instance_id: str, *, public_worktree_ref: str = "/tmp/public") -> dict:
    return {
        "instance_id": instance_id,
        "repo": "owner/repo",
        "base_commit": "abc123",
        "problem_statement": "Fix the issue.",
        "reference_patch": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n",
        "FAIL_TO_PASS": ["test_file.py::test_fix"],
        "PASS_TO_PASS": ["test_file.py::test_existing"],
        "public_worktree_ref": public_worktree_ref,
    }


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
    }
    assert [entry["instance_id"] for entry in report["curated"]] == [good]
    reasons = {entry["instance_id"]: entry["reason"] for entry in report["excluded"]}
    assert reasons[bad].startswith("dry_oracle_gold_not_runnable")
    assert reasons[missing] == "pro_script_missing"
    assert report["metric_applyable"] is False


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
