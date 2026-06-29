from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from supervisor.swe_bench_official_oracle import (
    _pro_test_list,
    run_swe_bench_pro_oracle,
)


AUTHORITY_FLAGS = {
    "metric_applyable": False,
    "improvement_claim_allowed": False,
    "powered_improvement_claim_allowed": False,
    "human_mergeability_claim_allowed": False,
    "default_change_allowed": False,
    "policy_mutated": False,
    "gate_advanced": False,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="ScaleAI/SWE-bench_Pro")
    parser.add_argument("--split", default="test")
    parser.add_argument(
        "--instance-id",
        default=(
            "instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111-v059c6fdc75567943479b23ebca7c07b5e9a7f34c"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="docs/dual-agent/pro-oracle-gold-proof-20260626/artifacts",
    )
    parser.add_argument("--scripts-dir", default="")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    preflight = _preflight()
    _write_json(output_dir / "vm-preflight.json", preflight)

    try:
        row = _load_dataset_row(args.dataset, args.split, args.instance_id)
        context = _context_from_row(row, scripts_dir=args.scripts_dir)
        os.environ["SWEBENCH_PRO_ORACLE_ARTIFACT_DIR"] = str(
            output_dir / "oracle-artifacts"
        )
        os.environ["SWEBENCH_PRO_ORACLE_RUN_ID_PREFIX"] = "pro-oracle-gold-proof"
        result = run_swe_bench_pro_oracle(context)
        _write_json(output_dir / "gold-result.json", result)
        manifest = _manifest(
            dataset=args.dataset,
            split=args.split,
            row=row,
            context=context,
            result=result,
            output_dir=output_dir,
            preflight=preflight,
        )
    except Exception as exc:  # pragma: no cover - live run artifact path
        manifest = _blocked_manifest(
            dataset=args.dataset,
            split=args.split,
            instance_id=args.instance_id,
            output_dir=output_dir,
            preflight=preflight,
            reason=f"{type(exc).__name__}: {exc}",
        )
    _write_json(output_dir / "gold-proof-manifest.json", manifest)
    return 0 if manifest["status"] == "completed" else 1


def _load_dataset_row(dataset: str, split: str, instance_id: str) -> dict[str, Any]:
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment artifact path
        raise RuntimeError("install datasets before running the live proof") from exc
    rows = load_dataset(dataset, split=split)
    for row in rows:
        if str(row.get("instance_id") or "") == instance_id:
            return dict(row)
    raise RuntimeError(f"instance not found in dataset: {instance_id}")


def _context_from_row(row: Mapping[str, Any], *, scripts_dir: str = "") -> dict[str, Any]:
    required = {
        "instance_id": str(row.get("instance_id") or ""),
        "repo": str(row.get("repo") or ""),
        "base_commit": str(row.get("base_commit") or ""),
        "patch": str(row.get("patch") or ""),
        "dockerhub_tag": str(row.get("dockerhub_tag") or ""),
    }
    missing = [key for key, value in required.items() if not value.strip()]
    fail_to_pass = _pro_test_list(row.get("fail_to_pass"))
    pass_to_pass = _pro_test_list(row.get("pass_to_pass"))
    selected = _pro_test_list(row.get("selected_test_files_to_run"))
    if not fail_to_pass:
        missing.append("fail_to_pass")
    if not pass_to_pass:
        missing.append("pass_to_pass")
    if not selected:
        missing.append("selected_test_files_to_run")
    if missing:
        raise RuntimeError("dataset row missing required fields: " + ",".join(missing))

    patch = required["patch"]
    context: dict[str, Any] = {
        "instance_id": required["instance_id"],
        "candidate_id": required["instance_id"] + "-gold-patch",
        "repo": required["repo"],
        "base_commit": required["base_commit"],
        "dockerhub_tag": required["dockerhub_tag"],
        "model_patch": patch,
        "official_patch": patch,
        "model_patch_sha256": sha256(patch.encode("utf-8")).hexdigest(),
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "selected_test_files_to_run": selected,
        "before_repo_set_cmd": str(row.get("before_repo_set_cmd") or ""),
    }
    if scripts_dir:
        context["swe_bench_pro_scripts_dir"] = str(Path(scripts_dir).expanduser())
    return context


def _preflight() -> dict[str, Any]:
    return {
        "platform_machine": platform.machine(),
        "platform_platform": platform.platform(),
        "docker_version": _command(["docker", "version", "--format", "{{json .}}"]),
        "docker_info": _command(["docker", "info", "--format", "{{json .}}"]),
        "docker_platform_env": {
            key: os.environ.get(key, "")
            for key in ("DOCKER_DEFAULT_PLATFORM", "SWEBENCH_PRO_ORACLE_DOCKER_PLATFORM")
        },
        "df": _command(["df", "-k", ".", "/tmp"]),
    }


def _command(command: Sequence[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - preflight artifact path
        return {
            "command": list(command),
            "return_code": 124,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "command": list(command),
        "return_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _manifest(
    *,
    dataset: str,
    split: str,
    row: Mapping[str, Any],
    context: Mapping[str, Any],
    result: Mapping[str, Any],
    output_dir: Path,
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = result.get("oracle_adapter_receipt")
    artifact_paths = receipt.get("artifact_paths") if isinstance(receipt, Mapping) else {}
    output_json = _read_artifact_json(artifact_paths, "output_json")
    patch_apply = _read_artifact_json(artifact_paths, "patch_apply_receipt")
    test_command = _read_artifact_json(artifact_paths, "test_command_receipt")
    parsed_tests = output_json.get("tests") if isinstance(output_json, Mapping) else []
    parsed_test_count = len(parsed_tests) if isinstance(parsed_tests, list) else 0
    fail_to_pass = _pro_test_list(row.get("fail_to_pass"))
    pass_to_pass = _pro_test_list(row.get("pass_to_pass"))
    selected = _pro_test_list(row.get("selected_test_files_to_run"))

    blockers: list[str] = []
    if result.get("fail_to_pass_status") != "pass":
        blockers.append("fail_to_pass_not_pass")
    if result.get("pass_to_pass_status") != "pass":
        blockers.append("pass_to_pass_not_pass")
    if not isinstance(patch_apply, Mapping) or patch_apply.get("patch_applied") is not True:
        blockers.append("patch_apply_not_proven")
    if not isinstance(test_command, Mapping) or test_command.get("test_command_return_code") != 0:
        blockers.append("test_command_return_code_nonzero")
    if parsed_test_count <= 0:
        blockers.append("parsed_tests_empty")
    if not fail_to_pass:
        blockers.append("fail_to_pass_empty")
    if not pass_to_pass:
        blockers.append("pass_to_pass_empty")
    if not selected:
        blockers.append("selected_tests_empty")

    return {
        "schema_version": "supervisor-pro-oracle-gold-proof/v1",
        "status": "completed" if not blockers else "unavailable",
        "blocked_reasons": blockers,
        "dataset": dataset,
        "split": split,
        "dataset_row": {
            "instance_id": str(row.get("instance_id") or ""),
            "repo": str(row.get("repo") or ""),
            "base_commit": str(row.get("base_commit") or ""),
            "dockerhub_tag": str(row.get("dockerhub_tag") or ""),
            "patch_sha256": sha256(str(row.get("patch") or "").encode("utf-8")).hexdigest(),
            "fail_to_pass_count": len(fail_to_pass),
            "pass_to_pass_count": len(pass_to_pass),
            "selected_test_count": len(selected),
        },
        "result_summary": {
            "fail_to_pass_status": result.get("fail_to_pass_status"),
            "pass_to_pass_status": result.get("pass_to_pass_status"),
            "patch_applied": result.get("patch_applied"),
            "test_command_return_code": test_command.get("test_command_return_code")
            if isinstance(test_command, Mapping)
            else None,
        },
        "parsed_test_count": parsed_test_count,
        "artifacts": _artifact_index(output_dir, artifact_paths),
        "preflight": preflight,
        "context_sha256": _sha256_json(context),
        "authority_flags": dict(AUTHORITY_FLAGS),
    }


def _blocked_manifest(
    *,
    dataset: str,
    split: str,
    instance_id: str,
    output_dir: Path,
    preflight: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-pro-oracle-gold-proof/v1",
        "status": "unavailable",
        "blocked_reasons": [reason],
        "dataset": dataset,
        "split": split,
        "dataset_row": {"instance_id": instance_id},
        "parsed_test_count": 0,
        "artifacts": _artifact_index(output_dir, {}),
        "preflight": preflight,
        "authority_flags": dict(AUTHORITY_FLAGS),
    }


def _artifact_index(output_dir: Path, artifact_paths: Any) -> dict[str, dict[str, Any]]:
    items = {"result": output_dir / "gold-result.json", "preflight": output_dir / "vm-preflight.json"}
    if isinstance(artifact_paths, Mapping):
        for key in (
            "patch",
            "run_script",
            "parser",
            "entryscript",
            "patch_apply_receipt",
            "test_command_receipt",
            "output_json",
            "stdout",
            "stderr",
        ):
            raw_path = artifact_paths.get(key)
            if raw_path:
                items[_manifest_artifact_key(key)] = Path(str(raw_path))
    indexed: dict[str, dict[str, Any]] = {}
    packet_dir = output_dir.parent
    for key, path in items.items():
        indexed[key] = _artifact_metadata(path, packet_dir=packet_dir)
    return indexed


def _manifest_artifact_key(key: str) -> str:
    return {
        "patch_apply_receipt": "patch_apply",
        "test_command_receipt": "test_command",
    }.get(key, key)


def _artifact_metadata(path: Path, *, packet_dir: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    exists = path.exists()
    try:
        display_path = str(path.relative_to(packet_dir))
    except ValueError:
        display_path = str(path)
    metadata: dict[str, Any] = {
        "path": display_path,
        "exists": exists,
    }
    if exists and path.is_file():
        payload = path.read_bytes()
        metadata["sha256"] = sha256(payload).hexdigest()
        metadata["bytes"] = len(payload)
    return metadata


def _read_artifact_json(artifact_paths: Any, key: str) -> dict[str, Any]:
    if not isinstance(artifact_paths, Mapping):
        return {}
    raw_path = artifact_paths.get(key)
    if not raw_path:
        return {}
    try:
        payload = json.loads(Path(str(raw_path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
