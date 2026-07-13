from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from supervisor.autoresearch.evaluator import run_evaluator_trials
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment


def _write_adversarial_evaluator(root: Path) -> tuple[str, str]:
    evaluator = root / "evaluators" / "adversarial_determinism.py"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(
        """
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--attempt-worktree", required=True)
parser.add_argument("--trial-index", required=True, type=int)
parser.add_argument("--metric-name", required=True)
parser.add_argument("--attempt-json", required=True)
args = parser.parse_args()

control_kind = os.environ.get("AUTORESEARCH_CONTROL_KIND", "")
if control_kind == "noop":
    metric = 0.4
elif control_kind == "harmful":
    metric = 0.3
elif control_kind == "determinism":
    counter = Path(os.environ["AUTORESEARCH_PROGRESS_PATH"]).with_suffix(
        ".determinism-counter"
    )
    run_index = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(run_index + 1), encoding="utf-8")
    metric = 0.75 if run_index == 0 else 0.25
else:
    metric = 0.75

status = "passed" if metric >= 0.5 else "failed"
print(json.dumps({
    "metric_name": args.metric_name,
    "metric_value": metric,
    "metrics": {
        args.metric_name: metric,
        "status": status,
    },
    "status": status,
    "output": {
        "decision": "accept" if status == "passed" else "reject",
    },
    "determinism_payload": {
        "schema_version": "adversarial-evaluator-projection/v1",
        "constant": True,
    },
    "cost_usd": 0.0,
}))
""".lstrip(),
        encoding="utf-8",
    )
    return (
        evaluator.relative_to(root).as_posix(),
        sha256(evaluator.read_bytes()).hexdigest(),
    )


def test_determinism_rejects_changed_result_hidden_by_constant_evaluator_projection(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "workspace" / "candidate.txt"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("candidate\n", encoding="utf-8")
    evaluator_ref, evaluator_hash = _write_adversarial_evaluator(tmp_path)
    experiment = AutoresearchExperiment(
        experiment_id="determinism-exp",
        task_id="determinism-task",
        hypothesis="Evaluator output must be stable across repeated execution.",
        baseline_ref="baseline:current",
        mutable_paths=("workspace",),
        immutable_paths=("locked",),
        evaluator_ref=evaluator_ref,
        evaluator_hash=evaluator_hash,
        metric_name="score",
        max_attempts=1,
        k_trials=1,
        budget_usd=1.0,
        timeout_s=5.0,
        execution_mode="live",
    )
    candidate_ref = candidate.relative_to(tmp_path).as_posix()
    attempt = AutoresearchAttempt(
        attempt_id="determinism-attempt",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-determinism",
        hypothesis="Adversarial evaluator projection must not be trusted.",
        changed_files=(candidate_ref,),
        metric_trials=(),
        metric_before=0.4,
        metric_after=0.75,
        metric_delta=0.35,
        metric_source="pending",
        policy_candidate_changes={
            ".supervisor/policy-overlay.yaml": candidate_ref,
        },
        artifact_hashes={
            candidate_ref: sha256(candidate.read_bytes()).hexdigest(),
        },
        evidence_refs=(f"artifact:{candidate_ref}",),
        status="pending",
    )

    execution = run_evaluator_trials(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    quality = execution.evaluator_quality
    determinism = quality["determinism"]
    assert quality["verdict"] == "rejected"
    assert determinism["verdict"] == "failed"
    assert determinism["verified"] is False
    assert (
        determinism["projection_schema_version"]
        == "supervisor-autoresearch-evaluator-determinism-projection/v1"
    )
    assert all(
        record["hash_source"] == "supervisor_canonical_projection"
        for record in determinism["records"]
    )
    assert determinism["projection_excluded_paths"] == [
        "determinism_payload",
    ]
    assert len(set(determinism["output_hashes"])) == 2
