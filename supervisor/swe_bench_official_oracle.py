"""Official SWE-bench oracle adapter for mergeability replay smoke runs."""
from __future__ import annotations

import ast
import json
import os
import platform as py_platform
import shlex
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence


def swe_bench_pro_oracle_scripts_dir(context: Mapping[str, Any] | None = None) -> Path:
    """Return the configured SWE-bench Pro per-instance scripts directory."""
    context = context or {}
    default_path = (
        Path(__file__).resolve().parent
        / "vendor"
        / "swe_bench_pro"
        / "run_scripts"
    )
    configured = (
        context.get("swe_bench_pro_scripts_dir")
        or os.environ.get("SWEBENCH_PRO_ORACLE_SCRIPTS_DIR")
    )
    configured_text = str(configured).strip() if configured is not None else ""
    return Path(configured_text or str(default_path)).expanduser()


def preflight_swe_bench_pro_run_scripts(
    instance_ids: Sequence[str],
    *,
    scripts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Check that every selected Pro instance has its bespoke scripts."""
    resolved_scripts_dir = (
        Path(scripts_dir).expanduser()
        if scripts_dir is not None
        else swe_bench_pro_oracle_scripts_dir()
    )
    checked: list[str] = []
    resolved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_instance_id in instance_ids:
        instance_id = str(raw_instance_id or "").strip()
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        checked.append(instance_id)
        instance_scripts_dir = resolved_scripts_dir / instance_id
        run_script = instance_scripts_dir / "run_script.sh"
        parser = instance_scripts_dir / "parser.py"
        missing_files = sorted(
            name
            for name, path in (
                ("run_script.sh", run_script),
                ("parser.py", parser),
            )
            if not path.exists()
        )
        if missing_files:
            missing.append({
                "instance_id": instance_id,
                "missing": missing_files,
                "scripts_dir": str(instance_scripts_dir),
            })
            continue
        run_script_bytes = run_script.read_bytes()
        parser_bytes = parser.read_bytes()
        resolved.append({
            "instance_id": instance_id,
            "run_script": str(run_script),
            "run_script_bytes": len(run_script_bytes),
            "run_script_sha256": sha256(run_script_bytes).hexdigest(),
            "parser": str(parser),
            "parser_bytes": len(parser_bytes),
            "parser_sha256": sha256(parser_bytes).hexdigest(),
        })
    reason = ""
    if missing:
        reason = "pro_script_missing:" + ";".join(
            f"{entry['instance_id']}({','.join(entry['missing'])})"
            for entry in missing
        )
    return {
        "ok": not missing,
        "scripts_dir": str(resolved_scripts_dir),
        "checked_instance_ids": checked,
        "missing_instance_ids": [entry["instance_id"] for entry in missing],
        "missing": missing,
        "resolved": resolved,
        "reason": reason,
    }


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
            reason="missing_model_patch",
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
    run_id = _safe_filename(
        run_id_prefix,
        instance_id,
        candidate_id,
        max_length=180,
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
            reason="official_oracle_timeout",
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
            reason=(
                "official_harness_failed"
                if completed.returncode != 0
                else "official_instance_report_missing"
            ),
        )

    payload = json.loads(instance_report_path.read_text(encoding="utf-8"))
    row = payload.get(instance_id) or {}
    tests_status = row.get("tests_status") if isinstance(row, Mapping) else {}
    fail_to_pass_status, fail_to_pass_reason = _status_for_with_reason(
        tests_status,
        "FAIL_TO_PASS",
    )
    pass_to_pass_status, pass_to_pass_reason = _status_for_with_reason(
        tests_status,
        "PASS_TO_PASS",
    )
    unavailable_reasons = [
        reason for reason in (fail_to_pass_reason, pass_to_pass_reason) if reason
    ]
    if unavailable_reasons:
        reason = (
            "official_report_status_bucket_unavailable:"
            + ",".join(unavailable_reasons)
        )
        receipt = _adapter_receipt(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact_paths=artifact_paths,
            fail_to_pass_status="unavailable",
            pass_to_pass_status="unavailable",
            dataset=dataset,
            split=split,
            run_id=run_id,
            oracle_unavailable=True,
            unavailable_reason=reason,
        )
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_unavailable": True,
            "oracle_unavailable_reason": reason,
            "oracle_adapter_receipt": receipt,
        }

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


def run_swe_bench_pro_oracle(context: Mapping[str, Any]) -> dict[str, Any]:
    """Run one frozen SWE-bench Pro candidate through the public Docker image.

    This adapter intentionally does not use ``swebench.harness.run_evaluation``:
    ``swebench==4.1.0`` cannot construct Pro test specs. The mergeability replay
    runner calls this only after decision freeze, and the adapter returns the
    same normalized status contract as the Verified harness adapter.
    """
    instance_id = str(context.get("instance_id") or "")
    candidate_id = str(context.get("candidate_id") or "")
    model_patch = str(context.get("model_patch") or "")
    if not model_patch.strip():
        model_patch = str(context.get("official_patch") or "")
    base_commit = str(context.get("base_commit") or "")

    artifact_root = Path(
        os.environ.get(
            "SWEBENCH_PRO_ORACLE_ARTIFACT_DIR",
            ".scratch/swebench-pro-oracle",
        )
    ).expanduser()
    run_id_prefix = os.environ.get(
        "SWEBENCH_PRO_ORACLE_RUN_ID_PREFIX",
        "supervisor-pro-oracle",
    )
    run_id = _safe_filename(
        run_id_prefix,
        instance_id or "missing-instance",
        candidate_id or "missing-candidate",
        max_length=180,
    )
    work_dir = artifact_root / _safe_fragment(instance_id) / _safe_fragment(candidate_id)
    workspace_dir = work_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    artifact_paths = {
        "patch": str(workspace_dir / "patch.diff"),
        "run_script": str(workspace_dir / "run_script.sh"),
        "parser": str(workspace_dir / "parser.py"),
        "entryscript": str(workspace_dir / "entryscript.sh"),
        "patch_apply_receipt": str(workspace_dir / "patch_apply.json"),
        "test_command_receipt": str(workspace_dir / "test_command.json"),
        "stdout": str(workspace_dir / "stdout.log"),
        "stderr": str(workspace_dir / "stderr.log"),
        "output_json": str(workspace_dir / "output.json"),
        "workspace": str(workspace_dir),
        "work_dir": str(work_dir),
    }

    missing_context = [
        key
        for key, value in (
            ("instance_id", instance_id),
            ("candidate_id", candidate_id),
            ("model_patch", model_patch),
            ("base_commit", base_commit),
        )
        if not str(value).strip()
    ]
    if missing_context:
        return _pro_adapter_failure(
            context=context,
            command=[],
            return_code=2,
            stdout="",
            stderr="missing SWE-bench Pro oracle context: " + ",".join(missing_context),
            artifact_paths=artifact_paths,
            reason="missing_pro_oracle_context:" + ",".join(missing_context),
            attempt_stage="harness",
        )

    scripts_dir = swe_bench_pro_oracle_scripts_dir(context)
    scripts_preflight = preflight_swe_bench_pro_run_scripts(
        [instance_id],
        scripts_dir=scripts_dir,
    )
    if not scripts_preflight["ok"]:
        return _pro_adapter_failure(
            context=context,
            command=[],
            return_code=2,
            stdout="",
            stderr="missing SWE-bench Pro scripts: " + scripts_preflight["reason"],
            artifact_paths=artifact_paths,
            reason=str(scripts_preflight["reason"]),
            attempt_stage="harness",
        )
    resolved_scripts = scripts_preflight["resolved"][0]
    source_run_script = Path(str(resolved_scripts["run_script"]))
    source_parser = Path(str(resolved_scripts["parser"]))
    source_script_evidence = dict(resolved_scripts)

    fail_to_pass = _pro_test_list(
        context.get("fail_to_pass")
        or context.get("FAIL_TO_PASS")
        or []
    )
    pass_to_pass = _pro_test_list(
        context.get("pass_to_pass")
        or context.get("PASS_TO_PASS")
        or []
    )
    selected_tests = _pro_test_list(context.get("selected_test_files_to_run") or [])
    before_repo_set_cmd = str(context.get("before_repo_set_cmd") or "")
    docker_image = _pro_docker_image(context)
    docker_platform = _pro_docker_platform()
    subprocess_timeout_s = int(
        float(os.environ.get("SWEBENCH_PRO_ORACLE_SUBPROCESS_TIMEOUT_S", "3600"))
    )

    cleaned_patch = _strip_binary_hunks(model_patch)
    (workspace_dir / "patch.diff").write_text(cleaned_patch, encoding="utf-8")
    (workspace_dir / "run_script.sh").write_text(
        source_run_script.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (workspace_dir / "parser.py").write_text(
        source_parser.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (workspace_dir / "entryscript.sh").write_text(
        _pro_entryscript(
            base_commit=base_commit,
            before_repo_set_cmd=before_repo_set_cmd,
            selected_tests=selected_tests,
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    docker_config = env.get("SWEBENCH_PRO_ORACLE_DOCKER_CONFIG")
    if docker_config:
        env["DOCKER_CONFIG"] = str(Path(docker_config).expanduser().resolve())

    pull_command = ["docker", "pull"]
    if docker_platform:
        pull_command.extend(["--platform", docker_platform])
    pull_command.append(docker_image)
    try:
        pull_result = subprocess.run(
            pull_command,
            cwd=work_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=subprocess_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return _pro_adapter_failure(
            context=context,
            command=pull_command,
            return_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"SWE-bench Pro docker pull timed out after {subprocess_timeout_s}s",
            artifact_paths=artifact_paths,
            reason="docker_pull_timeout",
            attempt_stage="docker",
            docker_image=docker_image,
            docker_platform=docker_platform,
            source_script_evidence=source_script_evidence,
        )
    if pull_result.returncode != 0:
        return _pro_adapter_failure(
            context=context,
            command=pull_command,
            return_code=pull_result.returncode,
            stdout=pull_result.stdout,
            stderr=pull_result.stderr,
            artifact_paths=artifact_paths,
            reason="docker_pull_failed",
            attempt_stage="docker",
            docker_image=docker_image,
            docker_platform=docker_platform,
            source_script_evidence=source_script_evidence,
        )

    run_command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace_dir.resolve()}:/workspace",
        "--entrypoint",
        "/bin/bash",
    ]
    if docker_platform:
        run_command.extend(["--platform", docker_platform])
    run_command.extend([docker_image, "-c", "bash /workspace/entryscript.sh"])
    try:
        run_result = subprocess.run(
            run_command,
            cwd=work_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=subprocess_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"SWE-bench Pro docker run timed out after {subprocess_timeout_s}s",
            artifact_paths=artifact_paths,
            reason="docker_run_timeout",
            attempt_stage="docker",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            source_script_evidence=source_script_evidence,
        )

    patch_applied = _pro_patch_applied(workspace_dir / "patch_apply.json")
    if patch_applied is not True:
        reason = (
            "patch_apply_failed"
            if patch_applied is False
            else "patch_apply_receipt_missing_or_malformed"
        )
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=run_result.returncode,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            artifact_paths=artifact_paths,
            reason=reason,
            attempt_stage="patch_apply",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            patch_applied=patch_applied,
            source_script_evidence=source_script_evidence,
        )

    output_path = workspace_dir / "output.json"
    if not output_path.exists():
        test_command_return_code = _pro_test_command_return_code(
            workspace_dir / "test_command.json"
        )
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=run_result.returncode,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            artifact_paths=artifact_paths,
            reason="pro_parser_output_missing",
            attempt_stage="scoring",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            patch_applied=patch_applied,
            test_command_return_code=test_command_return_code,
            source_script_evidence=source_script_evidence,
        )

    try:
        parser_payload = json.loads(output_path.read_text(encoding="utf-8"))
        parsed_test_count = _pro_test_count(parser_payload)
        passed_tests = _pro_passed_tests(parser_payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        test_command_return_code = _pro_test_command_return_code(
            workspace_dir / "test_command.json"
        )
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=run_result.returncode,
            stdout=run_result.stdout,
            stderr=f"{run_result.stderr}\n{exc}",
            artifact_paths=artifact_paths,
            reason="pro_parser_output_malformed",
            attempt_stage="scoring",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            patch_applied=patch_applied,
            test_command_return_code=test_command_return_code,
            source_script_evidence=source_script_evidence,
        )

    test_command_return_code = _pro_test_command_return_code(
        workspace_dir / "test_command.json"
    )
    if parsed_test_count == 0:
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=run_result.returncode,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            artifact_paths=artifact_paths,
            reason="pro_parser_output_empty",
            attempt_stage="scoring",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            patch_applied=True,
            test_command_return_code=test_command_return_code,
            source_script_evidence=source_script_evidence,
        )

    fail_to_pass_status = "pass" if set(fail_to_pass) <= passed_tests else "fail"
    pass_to_pass_status = "pass" if set(pass_to_pass) <= passed_tests else "fail"
    receipt = _pro_adapter_receipt(
        context=context,
        command=run_command,
        return_code=run_result.returncode,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status=fail_to_pass_status,
        pass_to_pass_status=pass_to_pass_status,
        docker_image=docker_image,
        docker_platform=docker_platform,
        attempt_stage="scoring",
        pull_command=pull_command,
        pull_return_code=pull_result.returncode,
        run_id=run_id,
        selected_tests=selected_tests,
        before_repo_set_cmd=before_repo_set_cmd,
        patch_applied=True,
        test_command_return_code=test_command_return_code,
        source_script_evidence=source_script_evidence,
    )
    return {
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "patch_applied": True,
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
    reason: str,
) -> dict[str, Any]:
    receipt = _adapter_receipt(
        context=context,
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status="unavailable",
        pass_to_pass_status="unavailable",
        dataset=os.environ.get(
            "SWEBENCH_OFFICIAL_ORACLE_DATASET",
            "SWE-bench/SWE-bench_Verified",
        ),
        split=os.environ.get("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "test"),
        run_id="",
        oracle_unavailable=True,
        unavailable_reason=reason,
    )
    return {
        "fail_to_pass_status": "unavailable",
        "pass_to_pass_status": "unavailable",
        "oracle_unavailable": True,
        "oracle_unavailable_reason": reason,
        "oracle_adapter_receipt": receipt,
    }


def _pro_adapter_failure(
    *,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
    reason: str,
    attempt_stage: str,
    docker_image: str = "",
    docker_platform: str = "",
    pull_command: list[str] | None = None,
    pull_return_code: int | None = None,
    patch_applied: bool | None = None,
    test_command_return_code: int | None = None,
    source_script_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = _pro_adapter_receipt(
        context=context,
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status="unavailable",
        pass_to_pass_status="unavailable",
        docker_image=docker_image,
        docker_platform=docker_platform,
        attempt_stage=attempt_stage,
        oracle_unavailable=True,
        unavailable_reason=reason,
        pull_command=pull_command,
        pull_return_code=pull_return_code,
        patch_applied=patch_applied,
        test_command_return_code=test_command_return_code,
        source_script_evidence=source_script_evidence,
        run_id="",
        selected_tests=_pro_test_list(context.get("selected_test_files_to_run") or []),
        before_repo_set_cmd=str(context.get("before_repo_set_cmd") or ""),
    )
    return {
        "fail_to_pass_status": "unavailable",
        "pass_to_pass_status": "unavailable",
        "oracle_unavailable": True,
        "oracle_unavailable_reason": reason,
        "patch_applied": patch_applied,
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
    oracle_unavailable: bool = False,
    unavailable_reason: str = "",
) -> dict[str, Any]:
    receipt = {
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
    if oracle_unavailable:
        receipt["oracle_unavailable"] = True
        receipt["unavailable_reason"] = unavailable_reason
    return receipt


def _pro_adapter_receipt(
    *,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
    fail_to_pass_status: str,
    pass_to_pass_status: str,
    docker_image: str,
    docker_platform: str,
    attempt_stage: str,
    run_id: str,
    selected_tests: Sequence[str],
    before_repo_set_cmd: str,
    oracle_unavailable: bool = False,
    unavailable_reason: str = "",
    pull_command: list[str] | None = None,
    pull_return_code: int | None = None,
    patch_applied: bool | None = None,
    test_command_return_code: int | None = None,
    source_script_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    docker_metadata: dict[str, Any] = {
        "image": docker_image,
        "attempt_stage": attempt_stage,
    }
    if docker_platform:
        docker_metadata["platform"] = docker_platform
    if pull_command is not None:
        docker_metadata["pull_command"] = list(pull_command)
    if pull_return_code is not None:
        docker_metadata["pull_return_code"] = int(pull_return_code)
    receipt = {
        "command": list(command),
        "return_code": int(return_code),
        "stdout_sha256": sha256(str(stdout).encode("utf-8")).hexdigest(),
        "stderr_sha256": sha256(str(stderr).encode("utf-8")).hexdigest(),
        "stdout_tail": str(stdout)[-4000:],
        "stderr_tail": str(stderr)[-4000:],
        "evaluator_version": "swe-bench-pro-local-docker",
        "harness": {
            "name": "swe-bench-pro-local-docker",
            "source": "scaleapi/SWE-bench_Pro-os",
            "pinned_commit": "ca10a60a5fcae51e6948ffe1485d4153d421e6c5",
            "run_id": run_id,
        },
        "docker": docker_metadata,
        "artifact_paths": dict(artifact_paths),
        "frozen_decisions_path": str(context.get("frozen_decisions_path") or ""),
        "frozen_decisions_sha256": str(context.get("frozen_decisions_sha256") or ""),
        "model_patch_sha256": str(context.get("model_patch_sha256") or ""),
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "selected_test_files_to_run": list(selected_tests),
        "before_repo_set_cmd_sha256": sha256(
            before_repo_set_cmd.encode("utf-8")
        ).hexdigest(),
    }
    if source_script_evidence is not None:
        receipt["source_run_scripts"] = dict(source_script_evidence)
    if patch_applied is not None:
        receipt["patch_applied"] = bool(patch_applied)
    if test_command_return_code is not None:
        receipt["test_command_return_code"] = int(test_command_return_code)
    if oracle_unavailable:
        receipt["oracle_unavailable"] = True
        receipt["unavailable_reason"] = unavailable_reason
    return receipt


def _status_for(tests_status: Any, key: str) -> str:
    status, _reason = _status_for_with_reason(tests_status, key)
    return status


def _status_for_with_reason(tests_status: Any, key: str) -> tuple[str, str]:
    if not isinstance(tests_status, Mapping):
        return "unavailable", "tests_status_missing_or_malformed"
    if key not in tests_status:
        return "unavailable", f"{key}_bucket_missing"
    bucket = tests_status.get(key)
    if not isinstance(bucket, Mapping):
        return "unavailable", f"{key}_bucket_malformed"
    failures = bucket.get("failure") or []
    if not isinstance(failures, Sequence) or isinstance(failures, (str, bytes)):
        return "unavailable", f"{key}_failure_malformed"
    return ("fail" if failures else "pass"), ""


def _pro_patch_applied(path: Path) -> bool | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("patch_applied")
    if isinstance(raw, bool):
        return raw
    return None


def _pro_test_command_return_code(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("test_command_return_code")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    return None


def _pro_entryscript(
    *,
    base_commit: str,
    before_repo_set_cmd: str,
    selected_tests: Sequence[str],
) -> str:
    """Build the container entryscript for one Pro candidate.

    The run_script is wrapped so its exit code is captured to
    ``test_command.json`` and the parser still runs on the captured logs.
    Without the wrapper, ``set -e`` would abort the script before
    ``parser.py`` writes ``output.json``, causing legitimate test failures
    to be misreported as ``pro_parser_output_missing`` instead of yielding
    a ``fail`` status from the parsed test results.
    """
    selected = ",".join(str(item) for item in selected_tests)
    selected_arg = shlex.quote(selected)
    pre_patch_commands, post_patch_commands = _split_pro_before_repo_set_cmd(
        before_repo_set_cmd
    )
    if not pre_patch_commands:
        pre_patch_commands = [
            f"git reset --hard {shlex.quote(base_commit)}",
            f"git checkout {shlex.quote(base_commit)}",
        ]

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "cd /app",
        *pre_patch_commands,
        "if git apply -v /workspace/patch.diff; then",
        "  printf '{\"patch_applied\": true}\\n' > /workspace/patch_apply.json",
        "else",
        "  status=$?",
        "  printf '{\"patch_applied\": false, \"return_code\": %s}\\n' \"$status\" > /workspace/patch_apply.json",
        "  exit \"$status\"",
        "fi",
    ]
    lines.extend(post_patch_commands)
    lines.extend([
        "test_command_exit=0",
        f"if bash /workspace/run_script.sh {selected_arg} > /workspace/stdout.log 2> /workspace/stderr.log; then",
        "  test_command_exit=0",
        "else",
        "  test_command_exit=$?",
        "fi",
        "printf '{\"test_command_return_code\": %s}\\n' \"$test_command_exit\" > /workspace/test_command.json",
        "python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json",
        "",
    ])
    return "\n".join(lines)


def _split_pro_before_repo_set_cmd(value: str) -> tuple[list[str], list[str]]:
    pre_patch: list[str] = []
    post_patch: list[str] = []
    for command in _pro_shell_commands(value):
        if _is_pro_pre_patch_repo_setup(command):
            pre_patch.append(command)
        else:
            post_patch.append(command)
    return pre_patch, post_patch


def _pro_shell_commands(value: str) -> list[str]:
    commands: list[str] = []
    for raw_line in str(value).splitlines():
        for raw_command in raw_line.split("&&"):
            command = raw_command.strip()
            if command:
                commands.append(command)
    return commands


def _is_pro_pre_patch_repo_setup(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    git_command = tokens[1]
    if git_command in {"reset", "clean"}:
        return True
    if git_command == "checkout":
        return "--" not in tokens
    return False


def _pro_docker_image(context: Mapping[str, Any]) -> str:
    explicit_image = str(context.get("docker_image") or "").strip()
    if explicit_image:
        return explicit_image
    tag = str(context.get("dockerhub_tag") or "").strip()
    username = str(
        context.get("dockerhub_username")
        or os.environ.get("SWEBENCH_PRO_ORACLE_DOCKERHUB_USERNAME")
        or "jefzda"
    )
    if not tag:
        tag = _pro_dockerhub_tag(
            instance_id=str(context.get("instance_id") or ""),
            repo=str(context.get("repo") or ""),
        )
    if "/" in tag and ":" in tag:
        return tag
    return f"{username}/sweap-images:{tag}"


def _pro_dockerhub_tag(*, instance_id: str, repo: str) -> str:
    if "/" not in repo:
        return _safe_fragment(instance_id)[:128]
    repo_base, repo_name = repo.lower().split("/", 1)
    hsh = instance_id.replace("instance_", "")
    if repo == "element-hq/element-web" and instance_id.endswith("-vnan"):
        repo_name = "element"
        hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]
    return f"{repo_base}.{repo_name}-{hsh}"[:128]


def _pro_docker_platform() -> str:
    configured = os.environ.get("SWEBENCH_PRO_ORACLE_DOCKER_PLATFORM")
    if configured:
        return configured
    if os.environ.get("SWEBENCH_PRO_ORACLE_AUTO_PLATFORM", "true").lower() in {
        "0",
        "false",
        "no",
    }:
        return ""
    try:
        if py_platform.machine().lower() in {"arm64", "aarch64"}:
            return "linux/amd64"
    except Exception:
        return ""
    return ""


def _pro_test_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return [text]
        return _pro_test_list(parsed)
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        return [str(item) for item in raw]
    return [str(raw)]


def _pro_passed_tests(payload: Any) -> set[str]:
    if not isinstance(payload, Mapping):
        raise ValueError("Pro parser output must be a JSON object")
    tests = payload.get("tests")
    if not isinstance(tests, Sequence) or isinstance(tests, (str, bytes)):
        raise ValueError("Pro parser output missing tests list")
    passed: set[str] = set()
    for index, raw_test in enumerate(tests):
        if not isinstance(raw_test, Mapping):
            raise ValueError(f"Pro parser test entry {index} must be an object")
        name = str(raw_test.get("name") or "")
        status = str(raw_test.get("status") or "").upper()
        if not name:
            raise ValueError(f"Pro parser test entry {index} missing name")
        if status == "PASSED":
            passed.add(name)
    return passed


def _pro_test_count(payload: Any) -> int:
    if not isinstance(payload, Mapping):
        raise ValueError("Pro parser output must be a JSON object")
    tests = payload.get("tests")
    if not isinstance(tests, Sequence) or isinstance(tests, (str, bytes)):
        raise ValueError("Pro parser output missing tests list")
    return len(tests)


def _last_nonempty_line(value: str) -> str:
    lines = [line.strip() for line in str(value).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _strip_binary_hunks(patch: str) -> str:
    sections = patch.split("diff --git ")
    if len(sections) == 1:
        return patch
    kept: list[str] = []
    prefix = sections[0]
    if prefix:
        kept.append(prefix)
    for section in sections[1:]:
        full = "diff --git " + section
        if "GIT binary patch" in full or "Binary files " in full:
            continue
        kept.append(full)
    return "".join(kept)


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


def _safe_filename(*fragments: str, max_length: int = 180) -> str:
    stem = "-".join(_safe_fragment(fragment) for fragment in fragments)
    if len(stem) <= max_length:
        return stem
    digest = sha256(
        "\0".join(str(fragment) for fragment in fragments).encode("utf-8")
    ).hexdigest()[:16]
    prefix_limit = max(1, max_length - len(digest) - 1)
    prefix = stem[:prefix_limit].rstrip("-_") or "artifact"
    return f"{prefix}-{digest}"
