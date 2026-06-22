"""Official SWE-bench oracle adapter for mergeability replay smoke runs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


def run_official_harness_oracle(context: Mapping[str, Any]) -> dict[str, Any]:
    """Run ``swebench.harness.run_evaluation`` for one frozen candidate.

    The mergeability replay runner calls this adapter only after it has written
    frozen decisions. The adapter writes its own prediction artifact, invokes
    the installed SWE-bench Docker harness, and returns the official report
    status plus receipt hashes to the replay report.
    """
    instance_id = _required_text(context, "instance_id")
    candidate_id = _required_text(context, "candidate_id")
    model_patch = str(context.get("model_patch") or "")
    if not model_patch.strip():
        model_patch = str(context.get("official_patch") or "")
    if not model_patch.strip():
        return _adapter_failure(
            context=context,
            command=[],
            return_code=2,
            stdout="",
            stderr="missing model_patch for official SWE-bench oracle",
            artifact_paths={},
        )

    artifact_root = Path(
        os.environ.get(
            "SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR",
            ".scratch/swebench-official-oracle",
        )
    ).expanduser()
    run_id_prefix = os.environ.get(
        "SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX",
        "supervisor-official-oracle",
    )
    run_id = (
        f"{_safe_fragment(run_id_prefix)}-"
        f"{_safe_fragment(instance_id)}-"
        f"{_safe_fragment(candidate_id)}"
    )
    work_dir = artifact_root / _safe_fragment(instance_id) / _safe_fragment(candidate_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    model_name = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_MODEL_NAME", "supervisor-replay")
    predictions_path = work_dir / "predictions.json"
    predictions_path.write_text(
        json.dumps(
            [
                {
                    "instance_id": instance_id,
                    "model_name_or_path": model_name,
                    "model_patch": model_patch,
                }
            ],
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    dataset = os.environ.get(
        "SWEBENCH_OFFICIAL_ORACLE_DATASET",
        "SWE-bench/SWE-bench_Verified",
    )
    split = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "test")
    timeout_s = int(float(os.environ.get("SWEBENCH_OFFICIAL_ORACLE_TIMEOUT_S", "600")))
    max_workers = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_MAX_WORKERS", "1")
    namespace = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_NAMESPACE", "swebench")
    cache_level = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_CACHE_LEVEL", "instance")
    clean = os.environ.get("SWEBENCH_OFFICIAL_ORACLE_CLEAN", "false")
    subprocess_timeout_s = int(
        float(os.environ.get("SWEBENCH_OFFICIAL_ORACLE_SUBPROCESS_TIMEOUT_S", "900"))
    )

    command = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset,
        "--split",
        split,
        "--instance_ids",
        instance_id,
        "--predictions_path",
        str(predictions_path.resolve()),
        "--max_workers",
        str(max_workers),
        "--timeout",
        str(timeout_s),
        "--cache_level",
        cache_level,
        "--clean",
        clean,
        "--run_id",
        run_id,
        "--namespace",
        namespace,
    ]

    env = os.environ.copy()
    docker_config = env.get("SWEBENCH_OFFICIAL_ORACLE_DOCKER_CONFIG")
    if docker_config:
        env["DOCKER_CONFIG"] = str(Path(docker_config).expanduser().resolve())

    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=subprocess_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return _adapter_failure(
            context=context,
            command=command,
            return_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"official SWE-bench oracle timed out after {subprocess_timeout_s}s",
            artifact_paths={
                "predictions": str(predictions_path),
                "work_dir": str(work_dir),
            },
        )

    model_dir = model_name.replace("/", "__")
    instance_report_path = (
        work_dir
        / "logs"
        / "run_evaluation"
        / run_id
        / model_dir
        / instance_id
        / "report.json"
    )
    final_report_path = work_dir / f"{model_dir}.{run_id}.json"
    test_output_path = instance_report_path.parent / "test_output.txt"
    run_log_path = instance_report_path.parent / "run_instance.log"
    artifact_paths = {
        "predictions": str(predictions_path),
        "final_report": str(final_report_path),
        "instance_report": str(instance_report_path),
        "test_output": str(test_output_path),
        "run_instance_log": str(run_log_path),
        "work_dir": str(work_dir),
    }
    if completed.returncode != 0 or not instance_report_path.exists():
        return _adapter_failure(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact_paths=artifact_paths,
        )

    payload = json.loads(instance_report_path.read_text(encoding="utf-8"))
    row = payload.get(instance_id) or {}
    tests_status = row.get("tests_status") if isinstance(row, Mapping) else {}
    fail_to_pass_status = _status_for(tests_status, "FAIL_TO_PASS")
    pass_to_pass_status = _status_for(tests_status, "PASS_TO_PASS")

    receipt = _adapter_receipt(
        context=context,
        command=command,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status=fail_to_pass_status,
        pass_to_pass_status=pass_to_pass_status,
        dataset=dataset,
        split=split,
        run_id=run_id,
    )
    return {
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "oracle_adapter_receipt": receipt,
    }


def _adapter_failure(
    *,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
) -> dict[str, Any]:
    receipt = _adapter_receipt(
        context=context,
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status="fail",
        pass_to_pass_status="fail",
        dataset=os.environ.get(
            "SWEBENCH_OFFICIAL_ORACLE_DATASET",
            "SWE-bench/SWE-bench_Verified",
        ),
        split=os.environ.get("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "test"),
        run_id="",
    )
    return {
        "fail_to_pass_status": "fail",
        "pass_to_pass_status": "fail",
        "oracle_adapter_receipt": receipt,
    }


def _adapter_receipt(
    *,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
    fail_to_pass_status: str,
    pass_to_pass_status: str,
    dataset: str,
    split: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "command": list(command),
        "return_code": int(return_code),
        "stdout_sha256": sha256(str(stdout).encode("utf-8")).hexdigest(),
        "stderr_sha256": sha256(str(stderr).encode("utf-8")).hexdigest(),
        "stdout_tail": str(stdout)[-4000:],
        "stderr_tail": str(stderr)[-4000:],
        "evaluator_version": "swebench.harness.run_evaluation",
        "harness": {
            "name": "swebench.harness.run_evaluation",
            "dataset": dataset,
            "split": split,
            "run_id": run_id,
        },
        "artifact_paths": dict(artifact_paths),
        "frozen_decisions_path": str(context.get("frozen_decisions_path") or ""),
        "frozen_decisions_sha256": str(context.get("frozen_decisions_sha256") or ""),
        "model_patch_sha256": str(context.get("model_patch_sha256") or ""),
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
    }


def _status_for(tests_status: Any, key: str) -> str:
    if not isinstance(tests_status, Mapping):
        return "fail"
    bucket = tests_status.get(key)
    if not isinstance(bucket, Mapping):
        return "pass"
    failures = bucket.get("failure") or []
    return "fail" if failures else "pass"


def _required_text(context: Mapping[str, Any], key: str) -> str:
    value = str(context.get(key) or "")
    if not value:
        raise ValueError(f"official SWE-bench oracle context missing {key}")
    return value


def _safe_fragment(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in str(value)
    ).strip("_") or "artifact"
