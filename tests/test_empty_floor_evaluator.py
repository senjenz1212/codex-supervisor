from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from supervisor.autoresearch.evaluator import run_evaluator_trials
from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment
from supervisor.state import State


BASE_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: base\n"
    "instruction_guidance_blocks: {}\n"
)
AFTER_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: candidate\n"
    "instruction_guidance_blocks:\n"
    "  outcome_review:\n"
    "    - Require measured empty-floor evidence.\n"
)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_policy_workspace(root: Path) -> Path:
    _write(root / ".supervisor" / "policy-overlay.yaml", BASE_OVERLAY)
    return _write(root / "workspace" / "policy-overlay.yaml", AFTER_OVERLAY)


def _write_evaluator(
    root: Path,
    *,
    crash_once: bool = False,
    fail_empty_floor: bool = False,
) -> tuple[str, str]:
    evaluator = root / "evaluators" / "empty_floor.py"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(
        f"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

attempt = json.loads(Path(args.attempt_json).read_text(encoding="utf-8"))
candidate_ref = next(iter((attempt.get("policy_candidate_changes") or {{}}).values()), "")
candidate_path = Path(args.attempt_worktree, candidate_ref)
candidate_text = candidate_path.read_text(encoding="utf-8") if candidate_path.exists() else ""
control_kind = os.environ.get("AUTORESEARCH_CONTROL_KIND", "")

if {fail_empty_floor!r} and os.environ.get("AUTORESEARCH_EMPTY_FLOOR") == "1":
    print("simulated empty-floor failure", file=sys.stderr)
    raise SystemExit(9)

if {crash_once!r} and not control_kind and candidate_text.strip() and args.trial_index == 1:
    sentinel = Path(os.environ["AUTORESEARCH_PROGRESS_PATH"]).with_suffix(".crashed-once")
    if not sentinel.exists():
        sentinel.write_text("crashed", encoding="utf-8")
        print("simulated one-time candidate crash", file=sys.stderr)
        raise SystemExit(7)

if control_kind == "noop":
    metric = 0.4
elif control_kind == "harmful":
    metric = 0.3
elif control_kind in {{"known_good", "determinism"}}:
    metric = 0.75
elif not candidate_text.strip():
    metric = 0.4
else:
    metric = {{0: 0.75, 1: 0.8, 2: 0.7}}.get(args.trial_index, 0.75)

print(json.dumps({{
    "metric_value": metric,
    "cost_usd": 0.001,
    "candidate_length": len(candidate_text),
    "control_kind": control_kind,
}}))
""".lstrip(),
        encoding="utf-8",
    )
    return evaluator.relative_to(root).as_posix(), _sha256(evaluator)


def _experiment(root: Path, *, evaluator_ref: str, evaluator_hash: str) -> AutoresearchExperiment:
    return AutoresearchExperiment(
        experiment_id="empty-floor-exp",
        task_id="empty-floor-task",
        hypothesis="Measure a stripped policy overlay before candidate trials.",
        baseline_ref="baseline:current",
        mutable_paths=("workspace",),
        immutable_paths=("locked",),
        evaluator_ref=evaluator_ref,
        evaluator_hash=evaluator_hash,
        metric_name="score",
        max_attempts=1,
        k_trials=3,
        budget_usd=1.0,
        timeout_s=5.0,
        execution_mode="live",
    )


def _attempt(candidate: Path, *, repo_root: Path) -> AutoresearchAttempt:
    candidate_rel = candidate.relative_to(repo_root).as_posix()
    return AutoresearchAttempt(
        attempt_id="empty-floor-attempt",
        experiment_id="empty-floor-exp",
        task_id="empty-floor-task",
        worker_id="worker-empty-floor",
        hypothesis="Candidate overlay should beat the empty floor.",
        changed_files=(candidate_rel,),
        metric_trials=(),
        metric_before=0.0,
        metric_source="pending",
        policy_candidate_changes={".supervisor/policy-overlay.yaml": candidate_rel},
        policy_overlay_candidate_ref=candidate_rel,
        artifact_hashes={candidate_rel: _sha256(candidate)},
        evidence_refs=(f"artifact:{candidate_rel}",),
        status="pending",
    )


def _write_fixture(root: Path, *, experiment: AutoresearchExperiment, attempt: AutoresearchAttempt) -> Path:
    fixture = root / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "experiment": experiment.to_payload(),
                "attempts": [attempt.to_payload()],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return fixture


def test_empty_floor_metric_populated_from_real_run(tmp_path):
    candidate = _write_policy_workspace(tmp_path)
    evaluator_ref, evaluator_hash = _write_evaluator(tmp_path, crash_once=True)
    experiment = _experiment(tmp_path, evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash)
    attempt = _attempt(candidate, repo_root=tmp_path)

    first = run_evaluator_trials(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
    )
    assert first.execution_errors

    execution = run_evaluator_trials(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert execution.execution_errors == ()
    assert execution.metric_trials == (0.75, 0.8, 0.7)
    assert execution.metric_before == 0.4
    assert execution.metric_after == 0.75
    assert execution.metric_delta == 0.35

    progress = json.loads(
        (tmp_path / "out" / "evaluator-runs" / "empty-floor-attempt.progress.json").read_text(
            encoding="utf-8"
        )
    )
    assert progress["empty_floor_metric"] == 0.4
    assert progress["metric_before"] == 0.4

    run_artifact = json.loads(Path(execution.evaluator_run_ref).read_text(encoding="utf-8"))
    assert run_artifact["empty_floor_metric"] == 0.4
    assert run_artifact["metric_before"] == 0.4
    assert run_artifact["metric_after"] == 0.75
    assert run_artifact["metric_delta"] == 0.35
    assert run_artifact["empty_floor_record"]["stdout_sha256"]


def test_empty_floor_failure_does_not_reuse_seed_as_floor(tmp_path):
    candidate = _write_policy_workspace(tmp_path)
    evaluator_ref, evaluator_hash = _write_evaluator(tmp_path, fail_empty_floor=True)
    experiment = _experiment(tmp_path, evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash)
    attempt = _attempt(candidate, repo_root=tmp_path)

    execution = run_evaluator_trials(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert execution.metric_trials == (0.75, 0.8, 0.7)
    assert execution.metric_before is None
    assert execution.metric_after == 0.75
    assert execution.metric_delta is None
    assert execution.execution_errors
    assert execution.execution_errors[0].startswith("empty_floor:")

    run_artifact = json.loads(Path(execution.evaluator_run_ref).read_text(encoding="utf-8"))
    assert run_artifact["empty_floor_metric"] is None
    assert run_artifact["metric_before"] is None
    assert run_artifact["metric_after"] == 0.75


def test_orchestrator_propagates_metric_before_after_delta(tmp_path):
    candidate = _write_policy_workspace(tmp_path)
    evaluator_ref, evaluator_hash = _write_evaluator(tmp_path)
    experiment = _experiment(tmp_path, evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash)
    attempt = _attempt(candidate, repo_root=tmp_path)
    fixture = _write_fixture(tmp_path, experiment=experiment, attempt=attempt)
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="empty-floor-live-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert record["metric_before"] == 0.4
    assert record["metric_after"] == 0.75
    assert record["metric_delta"] == 0.35
    assert record["empty_floor_comparison"] == {
        "metric_source": "evaluator_execution",
        "empty_floor_metric": 0.4,
        "candidate_metric": 0.75,
        "metric_delta": 0.35,
        "k_trials": 3,
    }

    durable_result = json.loads(
        (tmp_path / "out" / "evaluator-jobs" / "empty-floor-attempt" / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert durable_result["execution"]["metric_before"] == 0.4
    assert durable_result["execution"]["metric_after"] == 0.75
    assert durable_result["execution"]["metric_delta"] == 0.35


def test_live_run_yields_draft_proposal_with_quality_controls(tmp_path):
    target = _write(tmp_path / ".supervisor" / "policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path / "workspace" / "policy-overlay.yaml", AFTER_OVERLAY)
    evaluator_ref, evaluator_hash = _write_evaluator(tmp_path)
    experiment = _experiment(tmp_path, evaluator_ref=evaluator_ref, evaluator_hash=evaluator_hash)
    attempt = _attempt(candidate, repo_root=tmp_path)
    fixture = _write_fixture(tmp_path, experiment=experiment, attempt=attempt)
    state = State(str(tmp_path / "state.db"))

    report = run_autoresearch_fixture(
        fixture_path=fixture,
        state=state,
        run_id="empty-floor-derive-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        execution_mode="live",
    )

    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert record["evaluator_quality"]["verdict"] == "accepted"
    assert record["evaluator_quality"]["controls"]["noop"]["metric_before"] == 0.4
    assert record["evaluator_quality"]["controls"]["noop"]["metric_delta"] == 0.0
    assert record["evaluator_quality"]["controls"]["harmful"]["metric_delta"] == -0.1
    assert record["evaluator_quality"]["controls"]["known_good"]["metric_delta"] == 0.35
    assert report["derived_policy_proposals"][0]["status"] == "draft"

    events = state.read_events_since("empty-floor-derive-run", after_event_id=0, limit=100)
    proposal = next(event["payload"] for event in events if event["kind"] == "autoresearch_policy_proposal_created")
    assert proposal["status"] == "draft"
    assert proposal["source"] == "autoresearch_deriver"
    assert proposal["requires_operator_approval"] is True
    assert proposal["default_change_allowed"] is False
    assert proposal["automatic_policy_mutation"] is False
    assert proposal["gate_advanced"] is False
    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
