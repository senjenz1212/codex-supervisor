from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.autoresearch.report import build_autoresearch_report
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment
from supervisor.autoresearch.validation import validate_attempt
from supervisor.config import SupervisorCfg
from supervisor.state import State


FIXTURE = Path("tests/fixtures/autoresearch/fixture_experiment.json")
LOCKED_EVALUATOR = Path("tests/fixtures/autoresearch/locked/evaluator.py")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _experiment() -> AutoresearchExperiment:
    return AutoresearchExperiment(
        experiment_id="exp-1",
        task_id="task-1",
        hypothesis="Try a reviewer-rubric overlay.",
        baseline_ref="baseline:current",
        mutable_paths=("skills/reviewer-rubrics",),
        immutable_paths=("supervisor/state.py",),
        evaluator_ref=str(LOCKED_EVALUATOR),
        evaluator_hash=_sha256(LOCKED_EVALUATOR),
        metric_name="score",
        k_trials=3,
        timeout_s=5.0,
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
        "metric_source": "evaluator_execution",
        "evaluator_run_ref": "evaluator-runs/attempt-1.json",
        "evaluator_run_hash": "runhash",
        "patch_ref": "patches/attempt-1.patch",
        "artifact_hashes": {},
        "evidence_refs": ("evaluator_run:evaluator-runs/attempt-1.json",),
        "cost_usd": 0.01,
        "wall_clock_s": 5.0,
        "status": "pending",
    }
    base.update(overrides)
    return AutoresearchAttempt(**base)


def _write_fixture_evaluator(root: Path) -> None:
    evaluator = root / "tests" / "fixtures" / "autoresearch" / "locked" / "evaluator.py"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(
        LOCKED_EVALUATOR.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _write_evaluator(root: Path, body: str, *, name: str = "evaluator.py") -> tuple[str, str]:
    evaluator = root / "evaluators" / name
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(body, encoding="utf-8")
    return evaluator.relative_to(root).as_posix(), _sha256(evaluator)


def _write_fixture(root: Path, *, experiment: dict, attempts: list[dict]) -> Path:
    fixture = root / "autoresearch-fixture.json"
    fixture.write_text(
        json.dumps({"experiment": experiment, "attempts": attempts}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return fixture


def _live_experiment(*, evaluator_ref: str, evaluator_hash: str, **overrides) -> dict:
    base = {
        "experiment_id": "live-exp-1",
        "task_id": "live-task-1",
        "hypothesis": "Try an executable evaluator.",
        "baseline_ref": "baseline:current",
        "mutable_paths": ["workspace"],
        "immutable_paths": ["locked"],
        "evaluator_ref": evaluator_ref,
        "evaluator_hash": evaluator_hash,
        "metric_name": "score",
        "max_attempts": 1,
        "k_trials": 3,
        "budget_usd": 0.25,
        "timeout_s": 5.0,
        "execution_mode": "live",
    }
    base.update(overrides)
    return base


def _live_attempt(**overrides) -> dict:
    base = {
        "attempt_id": "live-attempt-1",
        "worker_id": "worker-live-1",
        "hypothesis": "Generate measured evidence.",
        "changed_files": ["workspace/evidence.md"],
        "patch_ref": "patches/live-attempt-1.patch",
        "artifact_hashes": {},
        "evidence_refs": [],
        "metric_trials": [999.0],
        "cost_usd": 0.0,
        "wall_clock_s": 0.0,
        "status": "pending",
    }
    base.update(overrides)
    return base


def _workflow_job_rows(state: State) -> list:
    return list(state._conn.execute("SELECT * FROM dual_agent_workflow_jobs ORDER BY created_at, job_id"))


def test_autoresearch_orchestrator_emits_experiment_and_attempt_events(tmp_path):
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=FIXTURE,
        state=state,
        run_id="run-autoresearch",
        repo_root=Path.cwd(),
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    events = state.read_events_since(run_id="run-autoresearch", after_event_id=0, limit=20)
    kinds = [
        event["kind"]
        for event in events
        if event["kind"] != "autoresearch_evaluator_job_phase"
        and str(event["kind"]).startswith("autoresearch_")
    ]
    assert kinds == [
        "autoresearch_experiment_started",
        "autoresearch_attempt_started",
        "autoresearch_attempt_execution_job_submitted",
        "autoresearch_attempt_execution_job_started",
        "autoresearch_attempt_execution_job_completed",
        "autoresearch_attempt_completed",
        "autoresearch_validation_started",
        "autoresearch_validation_completed",
        "autoresearch_report_emitted",
    ]
    submitted = next(event for event in events if event["kind"] == "autoresearch_attempt_execution_job_submitted")
    assert submitted["payload"]["attempt_id"] == "attempt-reviewer-rubric-001"
    validation = next(event for event in events if event["kind"] == "autoresearch_validation_completed")["payload"]
    assert validation["validation_status"] == "accepted"
    assert validation["metric_source"] == "evaluator_execution"
    assert validation["default_change_allowed"] is False
    assert report["default_change_allowed"] is False
    assert (tmp_path / "out" / "report.json").exists()


def test_autoresearch_live_evaluator_executes_through_durable_job_row(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()
print(json.dumps({"metric_value": 0.75 + (args.trial_index * 0.01), "cost_usd": 0.001}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash, k_trials=2),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="durable-evaluator-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    rows = _workflow_job_rows(state)
    assert len(rows) == 1
    assert rows[0]["recovery_point"] == "terminal"
    assert rows[0]["terminal_status"] == "completed"
    assert rows[0]["idempotency_token"] == "autoresearch:durable-evaluator-run:live-exp-1:live-attempt-1"
    assert "autoresearch_evaluator" in rows[0]["request_payload_json"]
    assert report["records"][0]["validation_status"] == "accepted"


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
        evaluator_ref=str(LOCKED_EVALUATOR),
        evaluator_hash=_sha256(LOCKED_EVALUATOR),
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
    evaluator = tmp_path / "tests" / "fixtures" / "autoresearch" / "locked" / "evaluator.py"
    evaluator.parent.mkdir(parents=True)
    evaluator.write_text('print("changed")\n', encoding="utf-8")

    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(),
        repo_root=tmp_path,
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "evaluator_hash_mismatch" in payload["gaming_flags"]
    assert payload["validation_errors"] == [
        "evaluator hash mismatch for tests/fixtures/autoresearch/locked/evaluator.py",
    ]


def test_autoresearch_validation_rejects_fixture_metrics_without_evaluator_execution():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(metric_source="fixture", evaluator_run_ref="", evaluator_run_hash=""),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "evaluator_not_executed" in payload["gaming_flags"]


def test_autoresearch_validation_flags_dangling_evidence_ref():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(evidence_refs=("artifact:missing.md",)),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "rejected"
    assert "dangling_evidence_ref" in payload["gaming_flags"]


def test_autoresearch_validation_flags_zero_variance_trials():
    report = validate_attempt(
        experiment=_experiment(),
        attempt=_attempt(metric_trials=(0.8, 0.8, 0.8)),
    )

    payload = report.to_payload()
    assert payload["validation_status"] == "accepted"
    assert "zero_variance_trials" in payload["gaming_flags"]
    assert payload["metric_iqr"] == 0.0


def test_autoresearch_live_evaluator_runs_k_trials_and_records_iqr(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

Path(args.attempt_worktree, "workspace", f"trial-{args.trial_index}.txt").write_text("ok\\n", encoding="utf-8")
print(json.dumps({"metric_value": 0.80 + (args.trial_index * 0.02), "cost_usd": 0.001}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="live-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert record["metric_trials"] == [0.8, 0.82, 0.84]
    assert record["metric_median"] == 0.82
    assert record["metric_iqr"] == 0.04
    assert record["metric_source"] == "evaluator_execution"
    assert record["evaluator_run_ref"]
    assert record["evaluator_run_hash"]
    assert (tmp_path / "out" / "evaluator-runs" / "live-attempt-1.json").exists()


def test_autoresearch_durable_evaluator_resumes_after_midrun_crash(tmp_path):
    repo = tmp_path / "repo"
    evaluator_ref, evaluator_hash = _write_evaluator(
        repo,
        """
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

counter = Path(os.environ["AUTORESEARCH_PROGRESS_PATH"]).with_suffix(f".trial-{args.trial_index}.count")
count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.parent.mkdir(parents=True, exist_ok=True)
counter.write_text(str(count + 1), encoding="utf-8")

sentinel = Path(os.environ["AUTORESEARCH_PROGRESS_PATH"]).with_suffix(".sentinel")
if args.trial_index == 1 and not sentinel.exists():
    sentinel.write_text("crashed-once", encoding="utf-8")
    print("simulated crash", file=sys.stderr)
    raise SystemExit(7)

print(json.dumps({"metric_value": 0.70 + (args.trial_index * 0.05), "cost_usd": 0.001}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash, k_trials=3),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    first = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="resume-run",
        repo_root=repo,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )
    assert first["records"][0]["validation_status"] == "rejected"

    second = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="resume-run",
        repo_root=repo,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    assert second["records"][0]["validation_status"] == "accepted"
    assert second["records"][0]["metric_trials"] == [0.7, 0.75, 0.8]
    assert (tmp_path / "out" / "evaluator-runs" / "live-attempt-1.progress.trial-0.count").read_text(
        encoding="utf-8"
    ) == "1"
    assert (tmp_path / "out" / "evaluator-runs" / "live-attempt-1.progress.trial-1.count").read_text(
        encoding="utf-8"
    ) == "2"


def test_autoresearch_live_evaluator_budget_overrun_is_flagged_and_rejected(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()
print(json.dumps({"metric_value": 0.9, "cost_usd": 0.2}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(
            evaluator_ref=evaluator_ref,
            evaluator_hash=evaluator_hash,
            k_trials=3,
            budget_usd=0.25,
        ),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="budget-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "budget_exceeded" in record["gaming_flags"]


def test_autoresearch_live_evaluator_timeout_is_flagged_and_rejected(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json, time

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()
time.sleep(0.2)
print(json.dumps({"metric_value": 0.9}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(
            evaluator_ref=evaluator_ref,
            evaluator_hash=evaluator_hash,
            k_trials=1,
            timeout_s=0.05,
        ),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="timeout-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "timeout" in record["gaming_flags"]


def test_autoresearch_live_evaluator_partial_progress_timeout_is_terminal(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json, os, time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

counter = Path(os.environ["AUTORESEARCH_PROGRESS_PATH"]).with_suffix(f".trial-{args.trial_index}.count")
count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.parent.mkdir(parents=True, exist_ok=True)
counter.write_text(str(count + 1), encoding="utf-8")

if args.trial_index == 1:
    time.sleep(1.0)
print(json.dumps({"metric_value": 0.7 + (args.trial_index * 0.05)}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(
            evaluator_ref=evaluator_ref,
            evaluator_hash=evaluator_hash,
            k_trials=3,
            timeout_s=0.5,
        ),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    first = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="partial-timeout-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )
    second = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="partial-timeout-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    rows = _workflow_job_rows(state)
    assert len(rows) == 1
    assert rows[0]["recovery_point"] == "terminal"
    assert rows[0]["terminal_status"] == "failed"
    assert first["records"][0]["validation_status"] == "rejected"
    assert second["records"][0]["validation_status"] == "rejected"
    assert "timeout" in first["records"][0]["gaming_flags"]
    assert "timeout" in second["records"][0]["gaming_flags"]
    assert (tmp_path / "out" / "evaluator-runs" / "live-attempt-1.progress.trial-0.count").read_text(
        encoding="utf-8"
    ) == "1"
    assert (tmp_path / "out" / "evaluator-runs" / "live-attempt-1.progress.trial-1.count").read_text(
        encoding="utf-8"
    ) == "1"


def test_autoresearch_default_replay_corpus_evaluator_produces_pass_rate(tmp_path):
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(
            evaluator_ref="",
            evaluator_hash="",
            metric_name="",
            k_trials=2,
            mutable_paths=["workspace"],
            immutable_paths=["locked"],
        ),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="default-replay-corpus-run",
        repo_root=Path.cwd(),
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert report["experiment"]["evaluator_ref"].endswith(
        "supervisor/autoresearch/evaluators/replay_corpus.py"
    )
    assert report["experiment"]["evaluator_hash"]
    assert record["metric_name"] == "pass_rate"
    assert record["metric_source"] == "evaluator_execution"
    assert len(record["metric_trials"]) == 2
    assert all(0.0 <= value <= 1.0 for value in record["metric_trials"])
    assert record["metric_iqr"] is not None


def test_autoresearch_live_evaluator_hash_mismatch_blocks_execution(tmp_path):
    marker = tmp_path / "marker.txt"
    evaluator_ref, _observed_hash = _write_evaluator(
        tmp_path,
        f"""
from pathlib import Path
Path({str(marker)!r}).write_text("executed", encoding="utf-8")
print('{{"metric_value": 1.0}}')
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash="0" * 64),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    result = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="hash-mismatch-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = result["records"][0]
    assert record["validation_status"] == "rejected"
    assert "evaluator_hash_mismatch" in record["gaming_flags"]
    assert not marker.exists()


def test_autoresearch_live_evaluator_ignores_restored_git_object_bookkeeping(tmp_path):
    git_object = tmp_path / ".git" / "objects" / "aa" / "autoresearch-bookkeeping"
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        f"""
from __future__ import annotations
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()

Path({str(git_object)!r}).parent.mkdir(parents=True, exist_ok=True)
Path({str(git_object)!r}).write_text("object bookkeeping\\n", encoding="utf-8")
print(json.dumps({{"metric_value": 0.9}}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash, k_trials=1),
        attempts=[_live_attempt(metric_trials=[])],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="git-object-bookkeeping-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert "outside_mutable_surface" not in record["gaming_flags"]
    assert not git_object.exists()


def test_autoresearch_live_evaluator_blocks_mutable_path_escape(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

Path("outside.txt").write_text("escape\\n", encoding="utf-8")
print(json.dumps({"metric_value": 0.9}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="escape-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "outside_mutable_surface" in record["gaming_flags"]
    assert not (tmp_path / "outside.txt").exists()


def test_autoresearch_live_evaluator_sanitizes_inherited_pwd(tmp_path, monkeypatch):
    monkeypatch.setenv("PWD", str(tmp_path))
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json, os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()

Path(os.environ["PWD"], "pwd-leak.txt").write_text("leak\\n", encoding="utf-8")
print(json.dumps({"metric_value": 0.9}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="pwd-sanitized-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "outside_mutable_surface" in record["gaming_flags"]
    assert not (tmp_path / "pwd-leak.txt").exists()


def test_autoresearch_live_evaluator_detects_absolute_source_checkout_mutation(tmp_path):
    source_leak = tmp_path / "absolute-source-leak.txt"
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        f"""
from __future__ import annotations
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()

Path({str(source_leak)!r}).write_text("leak\\n", encoding="utf-8")
print(json.dumps({{"metric_value": 0.9}}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="source-mutation-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "outside_mutable_surface" in record["gaming_flags"]
    assert any("source checkout" in error for error in record["validation_errors"])
    assert not source_leak.exists()


def test_autoresearch_live_evaluator_restores_source_checkout_on_failure(tmp_path):
    source_leak = tmp_path / "failed-source-leak.txt"
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        f"""
from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()

Path({str(source_leak)!r}).write_text("leak before failure\\n", encoding="utf-8")
print("not-json")
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="source-mutation-failure-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "outside_mutable_surface" in record["gaming_flags"]
    assert any("source checkout" in error for error in record["validation_errors"])
    assert not source_leak.exists()


def test_autoresearch_live_evaluator_detects_and_restores_git_hook_mutation(tmp_path):
    source_leak = tmp_path / ".git" / "hooks" / "autoresearch-leak"
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        f"""
from __future__ import annotations
import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()

Path({str(source_leak)!r}).parent.mkdir(parents=True, exist_ok=True)
Path({str(source_leak)!r}).write_text("hook leak\\n", encoding="utf-8")
print(json.dumps({{"metric_value": 0.9}}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="git-hook-mutation-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "rejected"
    assert "outside_mutable_surface" in record["gaming_flags"]
    assert any(".git/hooks/autoresearch-leak" in error for error in record["validation_errors"])
    assert not source_leak.exists()


def test_autoresearch_report_only_invariants_remain_false_for_live_run(tmp_path):
    evaluator_ref, evaluator_hash = _write_evaluator(
        tmp_path,
        """
from __future__ import annotations
import argparse, json

parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
parser.parse_args()
print(json.dumps({"metric_value": 0.91}))
""".lstrip(),
    )
    fixture = _write_fixture(
        tmp_path,
        experiment=_live_experiment(evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash),
        attempts=[_live_attempt()],
    )
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="live-report-only-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert report["default_change_allowed"] is False
    assert report["report_only"]["default_change_allowed"] is False
    assert report["report_only"]["policy_mutated"] is False
    assert record["default_change_allowed"] is False
    assert record["policy_mutated"] is False
    assert record["gate_advanced"] is False


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


def test_autoresearch_report_carries_policy_derivation_fields(tmp_path):
    _write_fixture_evaluator(tmp_path)
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("policy overlay candidate\n", encoding="utf-8")
    experiment = AutoresearchExperiment(
        experiment_id="exp-policy",
        task_id="task-policy",
        hypothesis="Try a policy overlay candidate.",
        baseline_ref="baseline:current",
        mutable_paths=("candidates",),
        immutable_paths=(),
        evaluator_ref=str(LOCKED_EVALUATOR),
        evaluator_hash=_sha256(tmp_path / LOCKED_EVALUATOR),
        metric_name="score",
        k_trials=3,
    )
    attempt = _attempt(
        experiment_id="exp-policy",
        task_id="task-policy",
        attempt_id="attempt-policy",
        changed_files=("candidates/policy-overlay.yaml",),
        metric_trials=(0.74, 0.8, 0.86),
        metric_before=0.7,
        policy_candidate_changes={
            ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
        },
        artifact_hashes={"candidates/policy-overlay.yaml": _sha256(candidate)},
        evidence_refs=(
            "evaluator_run:evaluator-runs/attempt-1.json",
            "artifact:candidates/policy-overlay.yaml",
        ),
    )

    validation = validate_attempt(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
    )
    report = build_autoresearch_report([validation])
    record = report["records"][0]

    assert record["validation_status"] == "accepted"
    assert record["metric_before"] == 0.7
    assert record["metric_after"] == 0.8
    assert record["metric_delta"] == 0.1
    assert record["policy_candidate_changes"] == {
        ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
    }


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
            "--execution-mode",
            "live",
            "--allow-live",
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
    assert report["records"][0]["metric_source"] == "evaluator_execution"
    assert (tmp_path / "out" / "evaluator-runs" / "attempt-reviewer-rubric-001.json").exists()


def test_autoresearch_cli_allow_live_executes_evaluator(tmp_path):
    repo_root = Path.cwd()
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_supervisor_autoresearch.py"),
            "--fixture",
            str(repo_root / FIXTURE),
            "--repo-root",
            str(repo_root),
            "--output-dir",
            str(tmp_path / "out"),
            "--run-id",
            "script-live-run",
            "--execution-mode",
            "live",
            "--allow-live",
        ],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert record["metric_source"] == "evaluator_execution"
    assert record["metric_trials"] == [0.86, 0.87, 0.88]


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
