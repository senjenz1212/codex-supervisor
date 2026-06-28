from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.autoresearch.durable_jobs import resolve_evaluator_defaults
from supervisor.autoresearch.policy_evolution import (
    PolicyEvolutionError,
    approve_policy_proposal,
    create_policy_evolution_proposals,
    derive_policy_evolution_proposals_from_report,
    report_contains_derivable_policy_record,
)
from supervisor.autoresearch.schema import AutoresearchExperiment
from supervisor.state import State


BASE_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: base\n"
    "instruction_guidance_blocks: {}\n"
)
AFTER_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: proposal-1\n"
    "instruction_guidance_blocks:\n"
    "  outcome_review:\n"
    "    - Verify runtime-native behavioral evidence before accepting.\n"
)
BENCH_ROOT = Path("tests/fixtures/mergeability_bench")
BEHAVIORAL_EVALUATOR = Path("supervisor/autoresearch/evaluators/mergeability_bench.py")
REPLAY_CORPUS_EVALUATOR = "supervisor/autoresearch/evaluators/replay_corpus.py"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _quality_controls() -> dict:
    return {
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "candidate_affects_evaluated_path": True,
        "determinism": {
            "source": "repeated_execution",
            "evidence_grade": "runtime_native",
            "supervisor_runtime_origin": "run_evaluator_quality_controls",
            "output_hashes": ["same-output", "same-output"],
        },
        "controls": {
            "noop": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.0,
            },
            "harmful": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": -0.1,
            },
            "known_good": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.2,
            },
        },
    }


def _report(*records: dict) -> dict:
    return {
        "schema_version": "supervisor-autoresearch-summary/v1",
        "report_sha256": "report-sha",
        "default_change_allowed": False,
        "report_only": {
            "default_change_allowed": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
        "records": list(records),
    }


def _applyable_record(**overrides) -> dict:
    record = {
        "schema_version": "supervisor-autoresearch-report/v1",
        "experiment_id": "exp-behavioral",
        "task_id": "task-behavioral",
        "attempt_id": "attempt-behavioral",
        "validation_status": "accepted",
        "recommendation": "validated as report-only candidate; operator review required",
        "metric_name": "mergeability_score",
        "metric_trials": [0.74, 0.82, 0.86],
        "metric_median": 0.82,
        "metric_iqr": 0.12,
        "metric_before": 0.62,
        "metric_after": 0.82,
        "metric_delta": 0.20,
        "empty_floor_comparison": {
            "metric_source": "evaluator_execution",
            "empty_floor_metric": 0.62,
            "candidate_metric": 0.82,
            "metric_delta": 0.20,
            "k_trials": 3,
        },
        "quality_unstable_across_trials": True,
        "metric_source": "evaluator_execution",
        "evaluator_ref": BEHAVIORAL_EVALUATOR.as_posix(),
        "evaluator_hash": _sha(Path.cwd() / BEHAVIORAL_EVALUATOR),
        "evaluator_run_ref": "docs/dual-agent/run/evaluator-runs/attempt-behavioral.json",
        "evaluator_run_hash": "evaluator-run-hash",
        "changed_files": ["candidates/policy-overlay.yaml"],
        "policy_overlay_candidate_ref": "candidates/policy-overlay.yaml",
        "policy_candidate_changes": {
            ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
        },
        "evaluator_quality": _quality_controls(),
        "gaming_flags": [],
        "validation_errors": [],
        "cost_usd": 0.19,
        "wall_clock_s": 12.5,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }
    record.update(overrides)
    return record


def _write_policy_files(root: Path) -> None:
    _write(root, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(root, "candidates/policy-overlay.yaml", AFTER_OVERLAY)


def _run_behavioral_evaluator(tmp_path: Path, *, candidate_fixture: str) -> dict:
    worktree = tmp_path / f"worktree-{candidate_fixture}"
    candidate_rel = "workspace/policy-overlay.yaml"
    candidate_source = Path.cwd() / BENCH_ROOT / "candidates" / f"{candidate_fixture}.json"
    candidate_path = worktree / candidate_rel
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(candidate_source.read_text(encoding="utf-8"), encoding="utf-8")
    attempt_json = tmp_path / f"attempt-{candidate_fixture}.json"
    attempt_json.write_text(
        json.dumps({
            "task_id": "calculator-addition",
            "policy_candidate_changes": {
                ".supervisor/policy-overlay.yaml": candidate_rel,
            },
            "policy_overlay_candidate_ref": candidate_rel,
        }),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path.cwd() / BEHAVIORAL_EVALUATOR),
            "--attempt-worktree",
            str(worktree),
            "--trial-index",
            "0",
            "--metric-name",
            "mergeability_score",
            "--attempt-json",
            str(attempt_json),
        ],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "AUTORESEARCH_SOURCE_ROOT": str(Path.cwd()),
            "MERGEABILITY_BENCH_ROOT": str((Path.cwd() / BENCH_ROOT).resolve()),
        },
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_behavioral_evaluator_reads_candidate(tmp_path):
    good = _run_behavioral_evaluator(tmp_path, candidate_fixture="known_good")
    bad = _run_behavioral_evaluator(tmp_path, candidate_fixture="known_bad")

    assert good["metric_value"] == 1.0
    assert bad["metric_value"] < good["metric_value"]


def test_replay_corpus_fixture_metric_rejected_as_adoption_signal(tmp_path):
    experiment = resolve_evaluator_defaults(
        AutoresearchExperiment(
            experiment_id="exp-default",
            task_id="task-default",
            hypothesis="default evaluator should be behavioral",
            baseline_ref="baseline:current",
            mutable_paths=("app/", "tests/"),
            immutable_paths=(),
            evaluator_ref="",
            evaluator_hash="",
            metric_name="",
        ),
        repo_root=Path.cwd(),
    )
    assert experiment.evaluator_ref == BEHAVIORAL_EVALUATOR.as_posix()
    assert experiment.metric_name == "mergeability_score"

    _write_policy_files(tmp_path)
    report = _report(_applyable_record(
        evaluator_ref=REPLAY_CORPUS_EVALUATOR,
        evaluator_hash="replay-corpus-hash",
    ))

    assert report_contains_derivable_policy_record(report, repo_root=tmp_path) is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    ) == []

    absolute_replay_report = _report(_applyable_record(
        evaluator_ref=str(Path.cwd() / REPLAY_CORPUS_EVALUATOR),
        evaluator_hash="replay-corpus-hash",
    ))
    assert report_contains_derivable_policy_record(absolute_replay_report, repo_root=tmp_path) is False
    assert derive_policy_evolution_proposals_from_report(
        absolute_replay_report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    ) == []


def test_benchmark_report_not_on_derivation_path(tmp_path):
    _write_policy_files(tmp_path)
    benchmark_record = _applyable_record(
        experiment_id="auto-evolve-benchmark-promotion",
        task_id="benchmark-to-autoresearch-evidence-conversion",
        metric_name="benchmark_evidence_conversion",
        benchmark_report_sha256="benchmark-report-sha",
    )
    report = _report(benchmark_record)

    assert report_contains_derivable_policy_record(report, repo_root=tmp_path) is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    ) == []
    assert create_policy_evolution_proposals(
        report,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml"},
        affected_gates=("outcome_review",),
    ) == []


def test_adoption_requires_named_operator(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write_policy_files(tmp_path)
    [proposal] = derive_policy_evolution_proposals_from_report(
        _report(_applyable_record()),
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )
    assert proposal["requires_operator_approval"] is True
    assert proposal["automatic_policy_mutation"] is False

    with pytest.raises(PolicyEvolutionError):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="codex-supervisor-axi",
            approval_channel="cli",
        )
    assert (tmp_path / ".supervisor/policy-overlay.yaml").read_text(encoding="utf-8") == BASE_OVERLAY
