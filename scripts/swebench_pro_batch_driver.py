#!/usr/bin/env python3
"""Batch driver for reproducible SWE-bench Pro corpus scaling.

This script pins the end-to-end execution seam around the already-pinned
per-attempt Claude Code runner:

1. curate a runnable instance roster with run_scripts preflight and optional
   dry gold-patch oracle runs;
2. run k>1 single-agent solver attempts per curated instance;
3. append dataset-reference gold backstops and oracle-label all applying
   candidates;
4. optionally run the powered factorial report over a panel-annotated corpus.

Live Docker/model work is opt-in. Without the explicit live flags, the script
writes a plan/manifest and refuses to spend solver or oracle budget.
"""
from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from supervisor.swe_bench_mergeability import (
    SwebenchMergeabilityFixtureRunnerError,
    build_swe_bench_pro_candidate_corpus,
    swebench_mergeability_powered_factorial_runner,
)
from supervisor.swe_bench_official_oracle import (
    preflight_swe_bench_pro_run_scripts,
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
DEFAULT_RUNNER_LABEL = "claude-code-litellm-haiku-real-swebench-pro-pilot"
DEFAULT_MODEL = "claude-3-5-haiku-20241022"
DEFAULT_PROVIDER = "anthropic_via_unity_litellm"
DEFAULT_DISK_FLOOR_GB = 15.0
DEFAULT_DOCKER_PRUNE_COMMAND = "docker image prune -af"
CHECKPOINT_SCHEMA_VERSION = "supervisor-swebench-pro-instance-checkpoint/v1"
BLOCKED_EXECUTION_SCHEMA_VERSION = "supervisor-swebench-pro-blocked-execution/v1"


@dataclass(frozen=True)
class BatchConfig:
    records_path: Path
    output_dir: Path
    powered_predictions_path: Path | None
    scripts_dir: str
    python_executable: str
    runner_command: str
    solver: str
    model: str
    provider: str
    k: int
    max_budget_usd: float
    min_good: int
    min_bad: int
    min_discordant: int
    alpha: float
    oracle_timeout_s: float
    run_dry_oracle: bool
    run_solver: bool
    run_labeling: bool
    run_powered: bool
    allow_live: bool
    prune_docker_between_instances: bool
    docker_prune_command: str
    disk_floor_gb: float
    resume_from_checkpoints: bool
    phase0_gate_decision_path: Path | None


class Phase0CleanHalt(RuntimeError):
    """A deliberate Phase 0 halt with evidence written before exit."""

    def __init__(self, *, reason: str, receipt_path: Path):
        super().__init__(reason)
        self.reason = reason
        self.receipt_path = receipt_path


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _read_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        records = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        raw = json.loads(text)
        records = raw if isinstance(raw, list) else raw.get("records", [])
    if not isinstance(records, list):
        raise ValueError("records input must be a JSON list or JSONL objects")
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"record {index} must be an object")
        instance_id = str(record.get("instance_id") or "").strip()
        if not instance_id:
            raise ValueError(f"record {index} missing instance_id")
        result.append(dict(record))
    return result


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_payload_hash(payload: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _atomic_write_text(
    path: Path,
    text: str,
    *,
    replace: Callable[[Path, Path], None] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    replace_func = replace or os.replace
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        replace_func(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_write_json(
    path: Path,
    payload: Any,
    *,
    replace: Callable[[Path, Path], None] | None = None,
) -> None:
    _atomic_write_text(path, _json_dumps(payload), replace=replace)


def _disk_free_gb(path: Path) -> float:
    target = path if path.exists() else path.parent
    usage = shutil.disk_usage(target)
    return usage.free / 1_000_000_000


def _parse_size_to_bytes(value: str) -> int | None:
    text = value.strip().replace(" ", "")
    if not text:
        return None
    units = [
        ("TB", 1_000_000_000_000),
        ("GB", 1_000_000_000),
        ("MB", 1_000_000),
        ("KB", 1_000),
        ("B", 1),
    ]
    upper = text.upper()
    for suffix, multiplier in units:
        if upper.endswith(suffix):
            number = upper[: -len(suffix)]
            try:
                return int(float(number) * multiplier)
            except ValueError:
                return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _docker_image_cache_bytes(cwd: Path) -> int | None:
    try:
        completed = subprocess.run(
            ["docker", "system", "df", "--format", "json"],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, Mapping):
            continue
        row_type = str(row.get("Type") or row.get("type") or "")
        if row_type.lower() != "images":
            continue
        for key in ("Size", "size", "TotalSize", "total_size"):
            parsed = _parse_size_to_bytes(str(row.get(key) or ""))
            if parsed is not None:
                return parsed
    return None


def _record_reference_patch(record: Mapping[str, Any]) -> str:
    for key in ("reference_patch", "model_patch", "patch", "gold_patch"):
        value = str(record.get(key) or "")
        if value.strip():
            return value
    raise ValueError(f"{record.get('instance_id')} missing reference patch")


def _oracle_context_for_gold(record: Mapping[str, Any], scripts_dir: str) -> dict[str, Any]:
    instance_id = str(record.get("instance_id") or "")
    return {
        "instance_id": instance_id,
        "candidate_id": f"{instance_id}-dataset-reference-gold-dry-oracle",
        "repo": str(record.get("repo") or ""),
        "dockerhub_tag": str(record.get("dockerhub_tag") or ""),
        "model_patch": _record_reference_patch(record),
        "base_commit": str(record.get("base_commit") or ""),
        "fail_to_pass": record.get("FAIL_TO_PASS") or record.get("fail_to_pass") or [],
        "pass_to_pass": record.get("PASS_TO_PASS") or record.get("pass_to_pass") or [],
        "selected_test_files_to_run": record.get("selected_test_files_to_run") or [],
        "before_repo_set_cmd": str(record.get("before_repo_set_cmd") or ""),
        "swe_bench_pro_scripts_dir": scripts_dir,
    }


def _tests_from_output_json(path_text: str) -> list[Any]:
    path = Path(path_text)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping):
        tests = raw.get("tests")
        return list(tests) if isinstance(tests, list) else []
    if isinstance(raw, list):
        return raw
    return []


def _oracle_tests_count(result: Mapping[str, Any]) -> int:
    for holder in (result, result.get("oracle_adapter_receipt")):
        if not isinstance(holder, Mapping):
            continue
        for key in ("tests", "parsed_tests"):
            value = holder.get(key)
            if isinstance(value, list):
                return len(value)
        artifact_paths = holder.get("artifact_paths")
        if isinstance(artifact_paths, Mapping):
            output_json = str(artifact_paths.get("output_json") or "")
            if output_json:
                return len(_tests_from_output_json(output_json))
    return 0


def _oracle_receipt(result: Mapping[str, Any]) -> Mapping[str, Any]:
    receipt = result.get("oracle_adapter_receipt")
    return receipt if isinstance(receipt, Mapping) else result


def _oracle_gold_runnable(result: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    receipt = _oracle_receipt(result)
    details = {
        "patch_applied": bool(receipt.get("patch_applied") is True),
        "fail_to_pass_status": str(receipt.get("fail_to_pass_status") or result.get("fail_to_pass_status") or ""),
        "pass_to_pass_status": str(receipt.get("pass_to_pass_status") or result.get("pass_to_pass_status") or ""),
        "fail_to_pass_count": int(
            receipt.get("fail_to_pass_count") or result.get("fail_to_pass_count") or 0
        ),
        "pass_to_pass_count": int(
            receipt.get("pass_to_pass_count") or result.get("pass_to_pass_count") or 0
        ),
        "pass_to_pass_empty_vacuous_pass": bool(
            receipt.get("pass_to_pass_empty_vacuous_pass") is True
            or result.get("pass_to_pass_empty_vacuous_pass") is True
        ),
        "test_command_return_code": receipt.get("test_command_return_code"),
        "tests_count": _oracle_tests_count(result),
        "rc_nonzero_resolved": False,
    }
    checks = {
        "patch_applied": details["patch_applied"],
        "fail_to_pass_status": details["fail_to_pass_status"] == "pass",
        "pass_to_pass_status": details["pass_to_pass_status"] == "pass",
        "test_command_return_code_present": details["test_command_return_code"]
        is not None,
        "tests_non_empty": int(details["tests_count"]) > 0,
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        return False, "dry_oracle_gold_not_runnable:" + ",".join(missing), details
    details["rc_nonzero_resolved"] = details["test_command_return_code"] not in (None, 0)
    return True, "", details


def _checkpoint_path(checkpoints_dir: Path, instance_id: str) -> Path:
    return checkpoints_dir / f"{_safe_fragment(instance_id)}.json"


def _checkpoint_payload(
    *,
    instance_id: str,
    decision: Mapping[str, Any],
    disk_free_gb: float | None,
    image_cache_bytes: int | None,
    prune_receipt: Mapping[str, Any] | None,
    now: Callable[[], str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "instance_id": instance_id,
        "ts": now(),
        "decision": dict(decision),
        "disk_free_gb": disk_free_gb,
        "image_cache_bytes": image_cache_bytes,
    }
    if prune_receipt is not None:
        payload["docker_prune"] = dict(prune_receipt)
    payload["payload_sha256"] = _stable_payload_hash(payload)
    return payload


def _verify_checkpoint(payload: Mapping[str, Any]) -> tuple[bool, str]:
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        return False, "schema_version"
    recorded_hash = str(payload.get("payload_sha256") or "")
    if not recorded_hash:
        return False, "payload_sha256_missing"
    unsigned = dict(payload)
    unsigned.pop("payload_sha256", None)
    actual_hash = _stable_payload_hash(unsigned)
    if actual_hash != recorded_hash:
        return False, "payload_sha256_mismatch"
    decision = payload.get("decision")
    if not isinstance(decision, Mapping):
        return False, "decision_missing"
    kind = str(decision.get("kind") or "")
    if kind not in {"curated", "excluded"}:
        return False, "decision_kind"
    return True, ""


def _load_checkpoint_receipts(
    checkpoints_dir: Path,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, str]]]:
    if not checkpoints_dir.exists():
        return {}, []
    valid: dict[str, Mapping[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    for path in sorted(checkpoints_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"path": str(path), "reason": type(exc).__name__})
            continue
        if not isinstance(payload, Mapping):
            invalid.append({"path": str(path), "reason": "not_object"})
            continue
        ok, reason = _verify_checkpoint(payload)
        instance_id = str(payload.get("instance_id") or "")
        if ok and instance_id:
            valid[instance_id] = payload
        else:
            invalid.append({
                "path": str(path),
                "instance_id": instance_id,
                "reason": reason,
            })
    return valid, invalid


def _checkpoint_decision_to_roster(
    receipt: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    decision = receipt.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("checkpoint receipt missing decision")
    kind = str(decision.get("kind") or "")
    if kind == "curated":
        entry = decision.get("entry")
        if not isinstance(entry, Mapping):
            raise ValueError("curated checkpoint missing entry")
        return kind, dict(entry)
    if kind == "excluded":
        exclusion = decision.get("exclusion")
        if not isinstance(exclusion, Mapping):
            raise ValueError("excluded checkpoint missing exclusion")
        return kind, dict(exclusion)
    raise ValueError(f"unknown checkpoint decision kind: {kind}")


def _is_enospc(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.ENOSPC


def _halt_receipt_path(output_dir: Path) -> Path:
    return output_dir / "phase0-blocked-execution-receipt.json"


def _write_blocked_execution_receipt(
    *,
    output_dir: Path,
    reason: str,
    disk_free_gb: float | None,
    disk_floor_gb: float,
    now: Callable[[], str],
    details: Mapping[str, Any] | None = None,
) -> Path:
    path = _halt_receipt_path(output_dir)
    receipt = {
        "schema_version": BLOCKED_EXECUTION_SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "timeline": [
            {
                "ts": now(),
                "event": "phase0_clean_halt",
                "reason": reason,
            }
        ],
        "disk_telemetry": {
            "disk_free_gb": disk_free_gb,
            "disk_floor_gb": disk_floor_gb,
        },
        "intervention_taken": "none; driver halted before spending solver/model budget",
        "artifacts_declared_invalid": [],
        "model_spend_usd": 0,
        "heartbeat_automation_removed": False,
        "details": dict(details or {}),
        **AUTHORITY_FLAGS,
    }
    _atomic_write_json(path, receipt)
    return path


def _check_disk_floor_or_halt(
    *,
    output_dir: Path,
    disk_floor_gb: float,
    disk_free_gb: Callable[[Path], float],
    now: Callable[[], str],
    instance_id: str,
) -> float:
    free_gb = float(disk_free_gb(output_dir))
    if free_gb < disk_floor_gb:
        receipt_path = _write_blocked_execution_receipt(
            output_dir=output_dir,
            reason="disk_floor_reached",
            disk_free_gb=free_gb,
            disk_floor_gb=disk_floor_gb,
            now=now,
            details={"instance_id": instance_id},
        )
        raise Phase0CleanHalt(reason="disk_floor_reached", receipt_path=receipt_path)
    return free_gb


def _maybe_disk_free(
    output_dir: Path | None,
    disk_free_gb: Callable[[Path], float],
) -> float | None:
    if output_dir is None:
        return None
    try:
        return float(disk_free_gb(output_dir))
    except OSError:
        return None


def _write_instance_checkpoint_or_halt(
    *,
    checkpoints_dir: Path,
    output_dir: Path,
    instance_id: str,
    decision: Mapping[str, Any],
    disk_free_gb: float | None,
    disk_floor_gb: float,
    image_cache_bytes: int | None,
    prune_receipt: Mapping[str, Any] | None,
    now: Callable[[], str],
) -> None:
    path = _checkpoint_path(checkpoints_dir, instance_id)
    payload = _checkpoint_payload(
        instance_id=instance_id,
        decision=decision,
        disk_free_gb=disk_free_gb,
        image_cache_bytes=image_cache_bytes,
        prune_receipt=prune_receipt,
        now=now,
    )
    try:
        _atomic_write_json(path, payload)
    except OSError as exc:
        reason = "checkpoint_write_enospc" if _is_enospc(exc) else "checkpoint_write_failed"
        receipt_path = _write_blocked_execution_receipt(
            output_dir=output_dir,
            reason=reason,
            disk_free_gb=disk_free_gb,
            disk_floor_gb=disk_floor_gb,
            now=now,
            details={
                "instance_id": instance_id,
                "checkpoint_path": str(path),
                "error": str(exc),
            },
        )
        raise Phase0CleanHalt(reason=reason, receipt_path=receipt_path) from exc


def _is_image_reclaiming_prune_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) < 3 or parts[0] != "docker":
        return False
    if parts[1:3] == ["image", "prune"]:
        return True
    if parts[1:3] == ["system", "prune"]:
        def _has_all_flag(part: str) -> bool:
            if part in {"-a", "--all"} or part.startswith("--all="):
                return True
            return part.startswith("-") and not part.startswith("--") and "a" in part[1:]

        return any(_has_all_flag(part) for part in parts[3:])
    if parts[1] == "rmi":
        return True
    return False


def _maybe_prune_after_instance(
    *,
    output_dir: Path | None,
    prune_docker_between_instances: bool,
    docker_prune_command: str,
    image_cache_bytes: Callable[[Path], int | None],
    prune_runner: Callable[..., Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    if not prune_docker_between_instances or output_dir is None:
        return None
    runner = prune_runner or _run_prune
    return runner(
        docker_prune_command,
        cwd=output_dir,
        image_cache_bytes=image_cache_bytes,
    )


def curate_roster(
    records: Sequence[Mapping[str, Any]],
    *,
    scripts_dir: str,
    run_dry_oracle: bool,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] = run_swe_bench_pro_oracle,
    output_dir: Path | None = None,
    resume_from_checkpoints: bool = False,
    disk_floor_gb: float = 0.0,
    disk_free_gb: Callable[[Path], float] = _disk_free_gb,
    image_cache_bytes: Callable[[Path], int | None] = _docker_image_cache_bytes,
    prune_docker_between_instances: bool = False,
    docker_prune_command: str = DEFAULT_DOCKER_PRUNE_COMMAND,
    prune_runner: Callable[..., Mapping[str, Any]] | None = None,
    now: Callable[[], str] = _utc_now_iso,
) -> dict[str, Any]:
    instance_ids = [str(record.get("instance_id") or "") for record in records]
    preflight = preflight_swe_bench_pro_run_scripts(
        instance_ids,
        scripts_dir=scripts_dir,
    )
    missing = set(preflight.get("missing_instance_ids") or [])
    curated: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    checkpoints_dir = output_dir / "checkpoints" if output_dir is not None else None
    valid_checkpoints: dict[str, Mapping[str, Any]] = {}
    invalid_checkpoints: list[dict[str, str]] = []
    resumed_from_checkpoint_count = 0
    if checkpoints_dir is not None and resume_from_checkpoints:
        valid_checkpoints, invalid_checkpoints = _load_checkpoint_receipts(checkpoints_dir)
    for record in records:
        instance_id = str(record.get("instance_id") or "")
        if instance_id in valid_checkpoints:
            try:
                kind, checkpoint_entry = _checkpoint_decision_to_roster(
                    valid_checkpoints[instance_id]
                )
            except ValueError as exc:
                invalid_checkpoints.append({
                    "instance_id": instance_id,
                    "reason": str(exc),
                })
            else:
                if kind == "curated":
                    curated.append(checkpoint_entry)
                else:
                    excluded.append(checkpoint_entry)
                resumed_from_checkpoint_count += 1
                continue
        disk_free_before: float | None = None
        if output_dir is not None and disk_floor_gb > 0:
            disk_free_before = _check_disk_floor_or_halt(
                output_dir=output_dir,
                disk_floor_gb=disk_floor_gb,
                disk_free_gb=disk_free_gb,
                now=now,
                instance_id=instance_id,
            )
        if instance_id in missing:
            exclusion = {
                "instance_id": instance_id,
                "reason": "pro_script_missing",
            }
            excluded.append(exclusion)
            decision = {"kind": "excluded", "exclusion": exclusion}
            if checkpoints_dir is not None:
                _write_instance_checkpoint_or_halt(
                    checkpoints_dir=checkpoints_dir,
                    output_dir=output_dir,
                    instance_id=instance_id,
                    decision=decision,
                    disk_free_gb=disk_free_before,
                    disk_floor_gb=disk_floor_gb,
                    image_cache_bytes=image_cache_bytes(output_dir),
                    prune_receipt=None,
                    now=now,
                )
            continue
        entry = {
            "instance_id": instance_id,
            "record": dict(record),
            "run_scripts_status": "present",
        }
        if run_dry_oracle:
            try:
                result = dict(oracle_runner(_oracle_context_for_gold(record, scripts_dir)))
                ok, reason, details = _oracle_gold_runnable(result)
            except Exception as exc:
                ok = False
                reason = f"dry_oracle_exception:{type(exc).__name__}"
                details = {"message": str(exc)}
            entry["dry_oracle"] = details
            if not ok:
                exclusion = {
                    "instance_id": instance_id,
                    "reason": reason,
                    "dry_oracle": details,
                }
                excluded.append(exclusion)
                decision = {"kind": "excluded", "exclusion": exclusion}
                prune_receipt = _maybe_prune_after_instance(
                    output_dir=output_dir,
                    prune_docker_between_instances=prune_docker_between_instances,
                    docker_prune_command=docker_prune_command,
                    image_cache_bytes=image_cache_bytes,
                    prune_runner=prune_runner,
                )
                if checkpoints_dir is not None:
                    _write_instance_checkpoint_or_halt(
                        checkpoints_dir=checkpoints_dir,
                        output_dir=output_dir,
                        instance_id=instance_id,
                        decision=decision,
                        disk_free_gb=_maybe_disk_free(output_dir, disk_free_gb),
                        disk_floor_gb=disk_floor_gb,
                        image_cache_bytes=(
                            image_cache_bytes(output_dir) if output_dir is not None else None
                        ),
                        prune_receipt=prune_receipt,
                        now=now,
                    )
                continue
        else:
            entry["dry_oracle"] = {"status": "not_run"}
        curated.append(entry)
        decision = {"kind": "curated", "entry": entry}
        prune_receipt = _maybe_prune_after_instance(
            output_dir=output_dir,
            prune_docker_between_instances=prune_docker_between_instances,
            docker_prune_command=docker_prune_command,
            image_cache_bytes=image_cache_bytes,
            prune_runner=prune_runner,
        )
        if checkpoints_dir is not None:
            _write_instance_checkpoint_or_halt(
                checkpoints_dir=checkpoints_dir,
                output_dir=output_dir,
                instance_id=instance_id,
                decision=decision,
                disk_free_gb=_maybe_disk_free(output_dir, disk_free_gb),
                disk_floor_gb=disk_floor_gb,
                image_cache_bytes=(
                    image_cache_bytes(output_dir) if output_dir is not None else None
                ),
                prune_receipt=prune_receipt,
                now=now,
            )
    vacuous_pass_to_pass_count = sum(
        1
        for entry in curated
        if isinstance(entry.get("dry_oracle"), Mapping)
        and entry["dry_oracle"].get("pass_to_pass_empty_vacuous_pass") is True
    )
    rc_nonzero_resolved_count = sum(
        1
        for entry in curated
        if isinstance(entry.get("dry_oracle"), Mapping)
        and entry["dry_oracle"].get("rc_nonzero_resolved") is True
    )
    return {
        "schema_version": "supervisor-swebench-pro-roster-curation/v1",
        "status": "completed",
        "preflight": preflight,
        "curated": curated,
        "excluded": excluded,
        "summary": {
            "input_instances": len(records),
            "curated_instances": len(curated),
            "excluded_instances": len(excluded),
            "dry_oracle_run": run_dry_oracle,
            "vacuous_pass_to_pass_count": vacuous_pass_to_pass_count,
            "rc_nonzero_resolved_count": rc_nonzero_resolved_count,
        },
        "provenance": {
            "checkpoint_dir": str(checkpoints_dir) if checkpoints_dir else "",
            "resume_from_checkpoints": resume_from_checkpoints,
            "resumed_from_checkpoint_count": resumed_from_checkpoint_count,
            "invalid_checkpoint_count": len(invalid_checkpoints),
            "invalid_checkpoints": invalid_checkpoints,
            "disk_floor_gb": disk_floor_gb,
            "docker_prune_command": docker_prune_command,
            "docker_prune_image_reclaiming": _is_image_reclaiming_prune_command(
                docker_prune_command
            ),
        },
        **AUTHORITY_FLAGS,
    }


def _public_worktree_ref(record: Mapping[str, Any]) -> str:
    value = str(record.get("public_worktree_ref") or record.get("public_worktree_path") or "")
    if not value:
        raise ValueError(
            f"{record.get('instance_id')} missing public_worktree_ref; "
            "materialize public bundles before running solver budget"
        )
    return value


def _generator_input(
    record: Mapping[str, Any],
    *,
    model: str,
    provider: str,
    budget: float,
) -> dict[str, Any]:
    public_ref = _public_worktree_ref(record)
    payload = {
        "schema_version": "supervisor-swebench-mergeability-live-generator-input/v1",
        "instance_id": str(record.get("instance_id") or ""),
        "repo": str(record.get("repo") or ""),
        "base_commit": str(record.get("base_commit") or ""),
        "problem_statement": str(record.get("problem_statement") or ""),
        "public_worktree_ref": public_ref,
        "public_checkout_ref": str(record.get("public_checkout_ref") or public_ref),
        "public_checkout_sha256": str(record.get("public_checkout_sha256") or ""),
        "public_worktree_sha256": str(record.get("public_worktree_sha256") or ""),
        "public_worktree_manifest": list(record.get("public_worktree_manifest") or []),
        "generation_config": {
            "model": model,
            "provider": provider,
            "budget_usd": float(budget),
        },
    }
    payload["generator_input_hash"] = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _run_solver_for_record(
    record: Mapping[str, Any],
    *,
    config: BatchConfig,
    solver_output_dir: Path,
) -> dict[str, Any]:
    instance_id = str(record.get("instance_id") or "")
    instance_dir = solver_output_dir / _safe_fragment(instance_id)
    instance_dir.mkdir(parents=True, exist_ok=True)
    input_path = instance_dir / "input.json"
    output_path = instance_dir / "solver-output.json"
    input_path.write_text(
        _json_dumps(_generator_input(
            record,
            model=config.model,
            provider=config.provider,
            budget=config.max_budget_usd,
        )),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["SWEBENCH_MERGEABILITY_GENERATOR_INPUT"] = str(input_path)
    env["SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT"] = str(output_path)
    command = [
        config.python_executable,
        "-m",
        "supervisor.swe_bench_solver",
        "--solver",
        config.solver,
        "--allow-live",
        "--max-budget-usd",
        str(config.max_budget_usd),
        "--k",
        str(config.k),
        "--runner-command",
        config.runner_command,
    ]
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "instance_id": instance_id,
            "status": "failed",
            "returncode": completed.returncode,
            "stderr_tail": completed.stderr[-4000:],
            "stdout_tail": completed.stdout[-4000:],
        }
    output = json.loads(output_path.read_text(encoding="utf-8"))
    attempts = output.get("attempts") if isinstance(output, Mapping) else []
    return {
        "instance_id": instance_id,
        "status": "completed",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
    }


def _run_prune(
    command: str,
    *,
    cwd: Path,
    image_cache_bytes: Callable[[Path], int | None] = _docker_image_cache_bytes,
) -> dict[str, Any]:
    before_bytes = image_cache_bytes(cwd)
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "command": command,
            "image_reclaiming_command": _is_image_reclaiming_prune_command(command),
            "image_cache_bytes_before": before_bytes,
            "image_cache_bytes_after": before_bytes,
            "reclaimed_bytes": None,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": f"{type(exc).__name__}: {exc}"[-2000:],
            "invocation_error": True,
        }
    after_bytes = image_cache_bytes(cwd)
    reclaimed_bytes = None
    if before_bytes is not None and after_bytes is not None:
        reclaimed_bytes = max(0, before_bytes - after_bytes)
    return {
        "command": command,
        "image_reclaiming_command": _is_image_reclaiming_prune_command(command),
        "image_cache_bytes_before": before_bytes,
        "image_cache_bytes_after": after_bytes,
        "reclaimed_bytes": reclaimed_bytes,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def run_solver_batch(
    curated: Sequence[Mapping[str, Any]],
    *,
    config: BatchConfig,
) -> dict[str, Any]:
    if not config.allow_live:
        raise RuntimeError("refusing solver batch without --allow-live")
    if not config.runner_command.strip():
        raise RuntimeError("refusing solver batch without --runner-command")
    solver_output_dir = config.output_dir / "solver"
    results: list[dict[str, Any]] = []
    prunes: list[dict[str, Any]] = []
    for entry in curated:
        record = entry.get("record") if isinstance(entry, Mapping) else entry
        if not isinstance(record, Mapping):
            raise ValueError("curated roster entry missing record")
        results.append(_run_solver_for_record(record, config=config, solver_output_dir=solver_output_dir))
        if config.prune_docker_between_instances:
            prunes.append(_run_prune(config.docker_prune_command, cwd=config.output_dir))
    return {
        "schema_version": "supervisor-swebench-pro-solver-batch/v1",
        "status": "completed" if all(r["status"] == "completed" for r in results) else "partial",
        "results": results,
        "docker_prunes": prunes,
        **AUTHORITY_FLAGS,
    }


def _load_solver_attempts(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for path in paths:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} must contain a solver output object")
        raw_attempts = raw.get("attempts")
        if not isinstance(raw_attempts, list):
            raise ValueError(f"{path} missing attempts list")
        attempts.extend(dict(item) for item in raw_attempts if isinstance(item, Mapping))
    return attempts


def _gold_attempt(record: Mapping[str, Any]) -> dict[str, Any]:
    instance_id = str(record.get("instance_id") or "")
    patch = _record_reference_patch(record)
    patch_hash = sha256(patch.encode("utf-8")).hexdigest()
    return {
        "instance_id": instance_id,
        "candidate_id": f"{instance_id}-dataset-reference-gold",
        "model_patch": patch,
        "candidate_artifact_hash": patch_hash,
        "model_patch_sha256": patch_hash,
        "diff_sha256": patch_hash,
        "origin": {
            "kind": "dataset_reference_patch",
            "dataset": "ScaleAI/SWE-bench_Pro",
            "split": "test",
        },
        "producer": {
            "kind": "dataset",
            "name": "ScaleAI/SWE-bench_Pro",
            "artifact": "reference_patch",
        },
    }


def _augment_attempt_with_oracle_context(
    attempt: Mapping[str, Any],
    record: Mapping[str, Any],
    scripts_dir: str,
) -> dict[str, Any]:
    enriched = dict(attempt)
    enriched["repo"] = str(record.get("repo") or "")
    enriched["dockerhub_tag"] = str(record.get("dockerhub_tag") or "")
    enriched["base_commit"] = str(record.get("base_commit") or "")
    enriched["FAIL_TO_PASS"] = (
        record.get("FAIL_TO_PASS") or record.get("fail_to_pass") or []
    )
    enriched["PASS_TO_PASS"] = (
        record.get("PASS_TO_PASS") or record.get("pass_to_pass") or []
    )
    enriched["selected_test_files_to_run"] = (
        record.get("selected_test_files_to_run") or []
    )
    enriched["before_repo_set_cmd"] = str(record.get("before_repo_set_cmd") or "")
    enriched["swe_bench_pro_scripts_dir"] = scripts_dir
    return enriched


def build_and_label_corpus(
    *,
    curated: Sequence[Mapping[str, Any]],
    solver_output_paths: Sequence[str | Path],
    output_path: Path,
    scripts_dir: str,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] = run_swe_bench_pro_oracle,
) -> dict[str, Any]:
    records_by_instance: dict[str, Mapping[str, Any]] = {}
    for entry in curated:
        record = entry.get("record") if isinstance(entry, Mapping) else entry
        if not isinstance(record, Mapping):
            continue
        instance_id = str(record.get("instance_id") or "")
        if instance_id:
            records_by_instance[instance_id] = record

    attempts = _load_solver_attempts(solver_output_paths)
    enriched_attempts: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts, start=1):
        instance_id = str(attempt.get("instance_id") or "")
        record = records_by_instance.get(instance_id)
        if record is None:
            raise RuntimeError(
                f"solver attempt {index} for instance_id={instance_id!r} has no "
                "matching curated dataset record; cannot supply Pro oracle context"
            )
        enriched_attempts.append(
            _augment_attempt_with_oracle_context(attempt, record, scripts_dir)
        )
    for record in records_by_instance.values():
        enriched_attempts.append(
            _augment_attempt_with_oracle_context(
                _gold_attempt(record), record, scripts_dir
            )
        )

    report = build_swe_bench_pro_candidate_corpus(
        attempts=enriched_attempts,
        output_path=output_path,
        oracle_runner=oracle_runner,
    )
    if not report.get("rows"):
        excluded_summary = [
            f"{entry.get('instance_id')}::{entry.get('candidate_id')}::{entry.get('reason')}"
            for entry in (report.get("excluded") or [])[:20]
        ]
        try:
            Path(output_path).unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(
            "Pro oracle labeling produced no applying candidates; refusing to keep "
            "an empty pro-predictions.jsonl after spending solver budget. "
            "First excluded entries: " + ", ".join(excluded_summary)
        )
    return report


def _safe_fragment(value: str) -> str:
    allowed = [ch if ch.isalnum() or ch in "._-" else "_" for ch in value]
    return "".join(allowed)[:180] or "instance"


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_json(path, payload)


def _assert_powered_predictions_ready(path: Path) -> None:
    """Fail fast if a corpus has not been through the panel stage yet."""
    required_mapping_keys = [
        "single_agent_baseline_decision",
        "same_model_multi_agent_decision",
        "hetero_multi_reviewer_decision",
        "runtime_evidence_floor_decision",
        "full_supervisor_stack_decision",
    ]
    missing: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, Mapping):
            missing.append(f"line {line_number}:not_object")
            continue
        candidate_id = str(row.get("candidate_id") or f"line-{line_number}")
        for key in required_mapping_keys:
            if not isinstance(row.get(key), Mapping):
                missing.append(f"{candidate_id}:{key}")
        reviewer_panel_results = row.get("reviewer_panel_results")
        if not (
            isinstance(reviewer_panel_results, Sequence)
            and not isinstance(reviewer_panel_results, (str, bytes, bytearray))
            and any(isinstance(item, Mapping) for item in reviewer_panel_results)
        ):
            missing.append(f"{candidate_id}:reviewer_panel_results")
    if missing:
        raise RuntimeError(
            "powered factorial requires a panel-annotated corpus; missing "
            + ", ".join(missing[:20])
        )


def _config_manifest(config: BatchConfig, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-swebench-pro-batch-driver-pin/v1",
        "status": "planned",
        "records_path": str(config.records_path),
        "records_sha256": _sha256_file(config.records_path),
        "input_instance_count": len(records),
        "output_dir": str(config.output_dir),
        "powered_predictions_path": (
            str(config.powered_predictions_path) if config.powered_predictions_path else ""
        ),
        "scripts_dir": config.scripts_dir,
        "runner_command": config.runner_command,
        "solver": config.solver,
        "model": config.model,
        "provider": config.provider,
        "k": config.k,
        "max_budget_usd": config.max_budget_usd,
        "thresholds": {
            "min_good": config.min_good,
            "min_bad": config.min_bad,
            "min_discordant": config.min_discordant,
            "alpha": config.alpha,
        },
        "phases": {
            "run_dry_oracle": config.run_dry_oracle,
            "run_solver": config.run_solver,
            "run_labeling": config.run_labeling,
            "run_powered": config.run_powered,
            "allow_live": config.allow_live,
            "prune_docker_between_instances": config.prune_docker_between_instances,
            "docker_prune_command": config.docker_prune_command,
            "docker_prune_image_reclaiming": _is_image_reclaiming_prune_command(
                config.docker_prune_command
            ),
            "disk_floor_gb": config.disk_floor_gb,
            "resume_from_checkpoints": config.resume_from_checkpoints,
        },
        "phase0_gate_decision_path": (
            str(config.phase0_gate_decision_path)
            if config.phase0_gate_decision_path
            else ""
        ),
        "labels": {
            "benchmark_oracle_kind": "swe_bench_held_out_test_pass_proxy",
            "cross_family_status": "operator_asserted_unverified_until_attestation_exists",
            "report_only": True,
        },
        **AUTHORITY_FLAGS,
    }


def _parse_args(argv: list[str] | None) -> BatchConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, help="JSON/JSONL SWE-bench Pro records")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--powered-predictions",
        default="",
        help=(
            "Panel-annotated predictions JSONL to use for --run-powered. "
            "The raw oracle-labeled corpus produced by --run-labeling is not enough."
        ),
    )
    parser.add_argument("--swe-bench-pro-scripts-dir", required=True)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--runner-command", default="")
    parser.add_argument("--solver", default=DEFAULT_RUNNER_LABEL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--max-budget-usd", type=float, default=0.0)
    parser.add_argument("--min-good", type=int, default=30)
    parser.add_argument("--min-bad", type=int, default=30)
    parser.add_argument("--min-discordant", type=int, default=25)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--oracle-timeout-s", type=float, default=3600.0)
    parser.add_argument("--run-dry-oracle", action="store_true")
    parser.add_argument("--run-solver", action="store_true")
    parser.add_argument("--run-labeling", action="store_true")
    parser.add_argument("--run-powered", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--prune-docker-between-instances", action="store_true")
    parser.add_argument("--docker-prune-command", default=DEFAULT_DOCKER_PRUNE_COMMAND)
    parser.add_argument("--disk-floor-gb", type=float, default=DEFAULT_DISK_FLOOR_GB)
    parser.add_argument(
        "--no-resume-from-checkpoints",
        action="store_true",
        help="Disable Phase 0 checkpoint resume and rerun all instances.",
    )
    parser.add_argument(
        "--phase0-gate-decision",
        default="",
        help=(
            "JSON gate artifact required before solver/model spend. It must set "
            "solver_spend_allowed=true."
        ),
    )
    args = parser.parse_args(argv)
    return BatchConfig(
        records_path=Path(args.records).expanduser(),
        output_dir=Path(args.output_dir).expanduser(),
        powered_predictions_path=(
            Path(args.powered_predictions).expanduser() if args.powered_predictions else None
        ),
        scripts_dir=args.swe_bench_pro_scripts_dir,
        python_executable=args.python_executable,
        runner_command=args.runner_command,
        solver=args.solver,
        model=args.model,
        provider=args.provider,
        k=args.k,
        max_budget_usd=args.max_budget_usd,
        min_good=args.min_good,
        min_bad=args.min_bad,
        min_discordant=args.min_discordant,
        alpha=args.alpha,
        oracle_timeout_s=args.oracle_timeout_s,
        run_dry_oracle=args.run_dry_oracle,
        run_solver=args.run_solver,
        run_labeling=args.run_labeling,
        run_powered=args.run_powered,
        allow_live=args.allow_live,
        prune_docker_between_instances=args.prune_docker_between_instances,
        docker_prune_command=args.docker_prune_command,
        disk_floor_gb=args.disk_floor_gb,
        resume_from_checkpoints=not args.no_resume_from_checkpoints,
        phase0_gate_decision_path=(
            Path(args.phase0_gate_decision).expanduser()
            if args.phase0_gate_decision
            else None
        ),
    )


def _assert_phase0_solver_spend_allowed(config: BatchConfig) -> None:
    path = config.phase0_gate_decision_path
    if path is None:
        raise RuntimeError(
            "Phase 0 gate decision is required before solver spend; pass "
            "--phase0-gate-decision with solver_spend_allowed=true"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"could not read Phase 0 gate decision {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Phase 0 gate decision is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("solver_spend_allowed") is not True:
        raise RuntimeError(
            "Phase 0 gate decision does not allow solver spend: "
            f"{path}"
        )


def main(argv: list[str] | None = None) -> int:
    config = _parse_args(argv)
    records = _read_records(config.records_path)
    if config.run_solver:
        _assert_phase0_solver_spend_allowed(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SWEBENCH_PRO_ORACLE_SCRIPTS_DIR"] = config.scripts_dir
    os.environ["SWEBENCH_PRO_ORACLE_SUBPROCESS_TIMEOUT_S"] = str(config.oracle_timeout_s)

    manifest = _config_manifest(config, records)
    _write_json(config.output_dir / "batch-driver-manifest.json", manifest)

    if (config.run_dry_oracle or config.run_solver or config.run_labeling) and not config.allow_live:
        raise RuntimeError("live oracle/solver/labeling phases require --allow-live")

    try:
        roster = curate_roster(
            records,
            scripts_dir=config.scripts_dir,
            run_dry_oracle=config.run_dry_oracle,
            output_dir=config.output_dir,
            resume_from_checkpoints=config.resume_from_checkpoints,
            disk_floor_gb=config.disk_floor_gb,
            prune_docker_between_instances=config.prune_docker_between_instances,
            docker_prune_command=config.docker_prune_command,
        )
    except Phase0CleanHalt as exc:
        summary = {
            "status": "blocked",
            "reason": exc.reason,
            "blocked_execution_receipt": str(exc.receipt_path),
            **AUTHORITY_FLAGS,
        }
        print(_json_dumps(summary), end="")
        return 2
    _write_json(config.output_dir / "curated-roster.json", roster)

    solver_report: dict[str, Any] | None = None
    if config.run_solver:
        solver_report = run_solver_batch(roster["curated"], config=config)
        _write_json(config.output_dir / "solver-batch-report.json", solver_report)

    corpus_report: dict[str, Any] | None = None
    predictions_path = config.output_dir / "pro-predictions.jsonl"
    if config.run_labeling:
        if solver_report is None:
            raise RuntimeError("--run-labeling requires --run-solver in this driver")
        solver_outputs = [
            result["output_path"]
            for result in solver_report["results"]
            if result.get("status") == "completed"
        ]
        if not solver_outputs:
            raise RuntimeError(
                "--run-labeling requires at least one completed solver instance; "
                f"solver batch reported {len(solver_report['results'])} attempts with "
                "zero completed. Refusing to write an empty pro-predictions.jsonl "
                "after spending solver budget."
            )
        corpus_report = build_and_label_corpus(
            curated=roster["curated"],
            solver_output_paths=solver_outputs,
            output_path=predictions_path,
            scripts_dir=config.scripts_dir,
        )
        _write_json(config.output_dir / "candidate-corpus-report.json", corpus_report)

    powered_report: dict[str, Any] | None = None
    if config.run_powered:
        powered_predictions_path = config.powered_predictions_path or predictions_path
        if config.powered_predictions_path is None and config.run_labeling:
            raise RuntimeError(
                "--run-powered requires --powered-predictions after the reviewer panel "
                "stage; the raw --run-labeling corpus is oracle-labeled but not "
                "panel-annotated"
            )
        if not powered_predictions_path.exists():
            raise RuntimeError(
                "--run-powered requires an existing panel-annotated predictions JSONL"
            )
        _assert_powered_predictions_ready(powered_predictions_path)
        powered_report = swebench_mergeability_powered_factorial_runner(
            predictions_path=powered_predictions_path,
            output_dir=config.output_dir / "powered-factorial",
            min_good=config.min_good,
            min_bad=config.min_bad,
            min_discordant=config.min_discordant,
            alpha=config.alpha,
        )

    summary = {
        "status": "completed",
        "manifest": str(config.output_dir / "batch-driver-manifest.json"),
        "curated_roster": str(config.output_dir / "curated-roster.json"),
        "curated_instances": roster["summary"]["curated_instances"],
        "excluded_instances": roster["summary"]["excluded_instances"],
        "solver_report": str(config.output_dir / "solver-batch-report.json") if solver_report else "",
        "candidate_corpus_report": str(config.output_dir / "candidate-corpus-report.json") if corpus_report else "",
        "powered_report": powered_report.get("report_path") if powered_report else "",
        **AUTHORITY_FLAGS,
    }
    print(_json_dumps(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
