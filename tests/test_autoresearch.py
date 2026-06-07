from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.autoresearch.report import build_autoresearch_report
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment
from supervisor.autoresearch.validation import validate_attempt
from supervisor.config import SupervisorCfg
from supervisor.state import State


FIXTURE = Path("tests/fixtures/autoresearch/fixture_experiment.json")


def _experiment() -> AutoresearchExperiment:
    return AutoresearchExperiment(
        experiment_id="exp-1",
        task_id="task-1",
        hypothesis="Try a reviewer-rubric overlay.",
        baseline_ref="baseline:current",
        mutable_paths=("skills/reviewer-rubrics",),
        immutable_paths=("supervisor/state.py",),
        evaluator_ref="tests/fixtures/autoresearch/locked/evaluator.json",
        evaluator_hash="3dc33d4148ae5324bdc6d9751ce255cecf121800c52a77fc788b7ca4ca5d352f",
        metric_name="score",
        k_trials=3,
    )


def _attempt(**overrides) -> AutoresearchAttempt:
    base = {
        "attempt_id": "attempt-1",
        "experiment_id": "exp-1",
        "task_id": "task-1",
        "worker_id": "worker-1",
        "hypothesis": "Add an evidence checklist.",
        "changed_files": ("skills/reviewer-rubrics/evidence.md",),
        "metric_trials": (0.82, 0.86, 0.84),
        "patch_ref": "patches/attempt-1.patch",
        "artifact_hashes": {},
        "evidence_refs": ("fixture:evidence",),
        "cost_usd": 0.01,
        "wall_clock_s": 5.0,
        "status": "pending",
    }
    base.update(overrides)
    return AutoresearchAttempt(**base)


def _write_fixture_evaluator(root: Path) -> None:
    evaluator = root / "tests" / "fixtures" / "autoresearch" / "locked" / "evaluator.json"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(
        '{\n  "metric_name": "reviewer_evidence_score",\n  "version": 1\n}\n',
        encoding="utf-8",
    )


def test_autoresearch_orchestrator_emits_experiment_and_attempt_events(tmp_path):
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=FIXTURE,
        state=state,
        run_id="run-autoresearch",
        repo_root=Path.cwd(),
        output_dir=tmp_path / "out",
    )

    events = state.read_events_since(run_id="run-autoresearch", after_event_id=0, limit=20)
    kinds = [event["kind"] for event in events]
    assert kinds == [
        "autoresearch_experiment_started",
        "autoresearch_attempt_started",
        "autoresearch_attempt_completed",
        "autoresearch_validation_started",
        "autoresearch_validation_completed",
        "autoresearch_report_emitted",
    ]
    assert events[2]["payload"]["attempt_id"] == "attempt-reviewer-rubric-001"
    validation = events[4]["payload"]
    assert validation["validation_status"] == "accepted"
    assert validation["default_change_allowed"] is False
    assert report["default_change_allowed"] is False
    assert (tmp_path / "out" / "report.json").exists()


def test_autoresearch_validation_rejects_immutable_path_mutation():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(changed_files=("supervisor/state.py",)),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "immutable_path_mutation" in payload["gaming_flags"]
    assert "outside_mutable_surface" in payload["gaming_flags"]
    assert payload["gate_advanced"] is False


def test_autoresearch_validation_accepts_mutable_only_attempt():
    report = validate_attempt(experiment=_experiment(), attempt=_attempt())

    payload = report.to_payload()
    assert payload["validation_status"] == "accepted"
    assert payload["gaming_flags"] == []
    assert payload["default_change_allowed"] is False


def test_autoresearch_validation_flags_missing_evidence_refs():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(evidence_refs=()),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "missing_evidence_refs" in payload["gaming_flags"]
    assert payload["gate_advanced"] is False


def test_autoresearch_validation_rejects_missing_artifact_hash_ref(tmp_path):
    _write_fixture_evaluator(tmp_path)
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(artifact_hashes={"skills/reviewer-rubrics/missing.md": "abc123"}),
        repo_root=tmp_path,
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "artifact_hash_mismatch" in payload["gaming_flags"]
    assert payload["validation_errors"] == [
        "artifact missing for skills/reviewer-rubrics/missing.md",
    ]


def test_autoresearch_validation_rejects_artifact_hash_mismatch(tmp_path):
    _write_fixture_evaluator(tmp_path)
    artifact = tmp_path / "skills" / "reviewer-rubrics" / "evidence.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("observed artifact\n", encoding="utf-8")

    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(artifact_hashes={"skills/reviewer-rubrics/evidence.md": "abc123"}),
        repo_root=tmp_path,
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "artifact_hash_mismatch" in payload["gaming_flags"]
    assert payload["validation_errors"] == [
        "artifact hash mismatch for skills/reviewer-rubrics/evidence.md",
    ]


def test_autoresearch_validation_rejects_path_traversal_to_immutable_surface():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(changed_files=("skills/reviewer-rubrics/../../supervisor/state.py",)),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "noncanonical_changed_path" in payload["gaming_flags"]
    assert "immutable_path_mutation" in payload["gaming_flags"]
    assert payload["changed_files"] == ["supervisor/state.py"]


def test_autoresearch_validation_rejects_absolute_immutable_path():
    absolute_path = Path.cwd() / "supervisor" / "state.py"
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(changed_files=(str(absolute_path),)),
        repo_root=Path.cwd(),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "noncanonical_changed_path" in payload["gaming_flags"]
    assert "immutable_path_mutation" in payload["gaming_flags"]
    assert payload["changed_files"] == ["supervisor/state.py"]


def test_autoresearch_validation_rejects_config_mutation_even_under_broad_mutable_prefix():
    experiment = AutoresearchExperiment(
        experiment_id="exp-config",
        task_id="task-config",
        hypothesis="Try changing production defaults.",
        baseline_ref="baseline:current",
        mutable_paths=("supervisor",),
        immutable_paths=(),
        evaluator_ref="tests/fixtures/autoresearch/locked/evaluator.json",
        evaluator_hash="3dc33d4148ae5324bdc6d9751ce255cecf121800c52a77fc788b7ca4ca5d352f",
        metric_name="score",
    )

    report = validate_attempt(
        experiment=experiment,
        attempt=_attempt(changed_files=("supervisor/config.py",)),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "immutable_path_mutation" in payload["gaming_flags"]
    assert "outside_mutable_surface" not in payload["gaming_flags"]


def test_autoresearch_validation_rejects_evaluator_hash_mismatch(tmp_path):
    evaluator = tmp_path / "tests" / "fixtures" / "autoresearch" / "locked" / "evaluator.json"
    evaluator.parent.mkdir(parents=True)
    evaluator.write_text('{"version": 999}\n', encoding="utf-8")

    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(),
        repo_root=tmp_path,
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "evaluator_hash_mismatch" in payload["gaming_flags"]
    assert payload["validation_errors"] == [
        "evaluator hash mismatch for tests/fixtures/autoresearch/locked/evaluator.json",
    ]


def test_autoresearch_report_reduces_trials_to_median_iqr_and_flags_unstable():
    validation = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(metric_trials=(0.7, 0.9, 0.8, 0.85)),
    )

    payload = validation.to_payload()
    assert payload["metric_median"] == 0.825
    assert payload["metric_iqr"] == 0.125
    assert payload["quality_unstable_across_trials"] is True


def test_autoresearch_report_is_report_only():
    validation = validate_attempt(experiment=_experiment(), attempt=_attempt())
    report = build_autoresearch_report([validation])

    assert report["default_change_allowed"] is False
    assert report["report_only"]["default_change_allowed"] is False
    assert report["report_only"]["policy_mutated"] is False
    assert report["recommendation"]["operator_review_required"] is True


def test_autoresearch_fixture_runner_blocks_live_calls_by_default(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_supervisor_autoresearch.py",
            "--fixture",
            str(FIXTURE),
            "--output-dir",
            str(tmp_path / "out"),
            "--execution-mode",
            "live",
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "live execution is disabled by default" in result.stderr


def test_autoresearch_fixture_runner_writes_report(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_supervisor_autoresearch.py",
            "--fixture",
            str(FIXTURE),
            "--output-dir",
            str(tmp_path / "out"),
            "--run-id",
            "script-run",
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["default_change_allowed"] is False
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["records"][0]["validation_status"] == "accepted"


def test_autoresearch_validator_cannot_advance_gates():
    payload = validate_attempt(experiment=_experiment(), attempt=_attempt()).to_payload()

    assert payload["gate_advanced"] is False
    assert payload["validation_status"] == "accepted"
    assert "dual_agent_gate_result" not in json.dumps(payload)


def test_autoresearch_cursor_reviewer_defaults_remain_compatible():
    cfg = SupervisorCfg(state_db=":memory:")
    validation = validate_attempt(experiment=_experiment(), attempt=_attempt()).to_payload()

    assert cfg.reviewer_output_mode == "cursor_sdk"
    assert validation["policy_mutated"] is False
    assert validation["default_change_allowed"] is False
