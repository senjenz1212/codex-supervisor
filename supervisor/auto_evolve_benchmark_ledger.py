"""JSONL ledger helpers for the auto-evolve benchmark bridge."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping


AUTO_EVOLVE_BENCHMARK_LEDGER_SCHEMA_VERSION = (
    "supervisor-auto-evolve-benchmark-ledger/v1"
)


AUTHORITY_FLAGS_FALSE: dict[str, bool] = {
    "metric_applyable": False,
    "improvement_claim_allowed": False,
    "powered_improvement_claim_allowed": False,
    "human_mergeability_claim_allowed": False,
    "policy_mutated": False,
    "gate_advanced": False,
}

REAL_ATTEMPT_STAGES = ("dataset_fetch", "docker", "harness", "scoring")
ATTEMPT_STAGES = ("recon", *REAL_ATTEMPT_STAGES)


def append_auto_evolve_benchmark_event(
    ledger_path: str | Path,
    *,
    stage: str,
    promise_ids: Iterable[str],
    status: str,
    artifact_path: str | Path | None = None,
    aeb0_artifact_ref: str | Path | None = None,
    blocked_reasons: Iterable[str] = (),
    dataset: str = "",
    split: str = "",
    dataset_revision: str = "",
    dataset_sha: str = "",
    allow_known_contaminated_dataset: bool = False,
    dataset_contamination_caveat: str = "",
    instance_ids: Iterable[str] = (),
    harness_commands: Iterable[Iterable[str] | str] = (),
    report_paths: Iterable[str | Path] = (),
    candidate_hashes: Mapping[str, str] | None = None,
    attempt_stage: str = "",
    metric_source: str = "diagnostic_report",
    sink_status: str = "not_applicable",
    annotation_queue_status: str = "deferred",
    operator_review_required: bool = True,
) -> dict[str, Any]:
    """Append one report-only benchmark bridge ledger event."""
    ledger = Path(ledger_path).expanduser()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    artifact = _artifact_record(artifact_path)
    aeb0_ref = str(aeb0_artifact_ref or "")
    record: dict[str, Any] = {
        "schema_version": AUTO_EVOLVE_BENCHMARK_LEDGER_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "stage": str(stage),
        "promise_ids": [str(promise_id) for promise_id in promise_ids],
        "status": str(status),
        "metric_source": str(metric_source),
        "dataset": {
            "name": str(dataset),
            "split": str(split),
            "revision": str(dataset_revision),
            "sha": str(dataset_sha),
            "allow_known_contaminated": bool(allow_known_contaminated_dataset),
            "contamination_caveat": str(dataset_contamination_caveat),
        },
        "instance_ids": [str(instance_id) for instance_id in instance_ids],
        "harness_commands": [_command_record(command) for command in harness_commands],
        "report_paths": [str(path) for path in report_paths],
        "candidate_hashes": {
            str(key): str(value)
            for key, value in dict(candidate_hashes or {}).items()
        },
        "attempt_stage": str(attempt_stage),
        "artifact": artifact,
        "aeb0_dependency": _aeb0_dependency_record(aeb0_ref),
        "blocked_reasons": [
            str(reason) for reason in blocked_reasons if str(reason)
        ],
        "authority_flags": dict(AUTHORITY_FLAGS_FALSE),
        "operator_review_required": bool(operator_review_required),
        "sink_status": str(sink_status),
        "annotation_queue_status": str(annotation_queue_status),
    }
    errors = validate_auto_evolve_benchmark_ledger_record(record)
    if errors:
        raise ValueError("; ".join(errors))
    record["record_sha256"] = _sha256_json({
        key: value for key, value in record.items() if key != "record_sha256"
    })
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def validate_auto_evolve_benchmark_ledger_record(
    record: Mapping[str, Any],
) -> list[str]:
    """Return schema/provenance errors for one benchmark bridge ledger row."""
    errors: list[str] = []
    if record.get("schema_version") != AUTO_EVOLVE_BENCHMARK_LEDGER_SCHEMA_VERSION:
        errors.append("schema_version is invalid")
    dataset = record.get("dataset")
    if not isinstance(dataset, Mapping):
        errors.append("dataset declaration is required")
        dataset = {}
    if not str(dataset.get("name") or ""):
        errors.append("dataset.name is required")
    if not str(dataset.get("split") or ""):
        errors.append("dataset.split is required")
    if not (str(dataset.get("revision") or "") or str(dataset.get("sha") or "")):
        errors.append("dataset.revision_or_sha is required")
    dataset_name = str(dataset.get("name") or "").lower().replace("_", "-")
    if "swe-bench-verified" in dataset_name or (
        "verified" in dataset_name and "swe-bench" in dataset_name
    ):
        if not (
            bool(dataset.get("allow_known_contaminated"))
            and str(dataset.get("contamination_caveat") or "")
        ):
            errors.append("known_contaminated_dataset_requires_override_and_caveat")
    attempt_stage = str(record.get("attempt_stage") or "")
    if not attempt_stage:
        errors.append("attempt_stage is required")
    elif attempt_stage not in ATTEMPT_STAGES:
        errors.append("attempt_stage is invalid")
    flags = record.get("authority_flags")
    if not isinstance(flags, Mapping):
        errors.append("authority_flags are required")
    else:
        for key, expected in AUTHORITY_FLAGS_FALSE.items():
            if flags.get(key) is not expected:
                errors.append(f"authority_flags.{key} must be false")
    if record.get("operator_review_required") is not True:
        errors.append("operator_review_required must be true")
    return errors


def validate_aeb0_real_attempt_artifact(path: str | Path) -> dict[str, Any]:
    """Validate that an AEB-0 artifact reached a real empirical attempt stage."""
    artifact = Path(path).expanduser()
    reasons: list[str] = []
    if not artifact.exists() or not artifact.is_file():
        return {"valid": False, "reasons": ["artifact_missing"]}
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    gate = payload.get("aeb0_artifact_gate") if isinstance(payload, Mapping) else {}
    blocked = []
    if isinstance(gate, Mapping):
        blocked = [str(reason) for reason in gate.get("blocked_reasons") or []]
    blocked.extend(str(reason) for reason in payload.get("blocked_reasons") or [])
    blocked.extend(str(reason) for reason in payload.get("metrics_unavailable_reasons") or [])
    if any(reason.startswith("missing_cli_prerequisite:") for reason in blocked):
        reasons.append("pre_run_cli_guard_is_not_real_attempt")
    attempt_stage = str(payload.get("attempt_stage") or "")
    if not attempt_stage:
        reasons.append("attempt_stage_required")
    elif attempt_stage not in ATTEMPT_STAGES:
        reasons.append("attempt_stage_invalid")
    elif attempt_stage not in REAL_ATTEMPT_STAGES:
        reasons.append("attempt_stage_did_not_reach_dataset_or_docker")
    return {
        "valid": not reasons,
        "reasons": sorted(set(reasons)),
        "attempt_stage": attempt_stage,
    }


def _artifact_record(path_value: str | Path | None) -> dict[str, Any]:
    if not path_value:
        return {"path": "", "exists": False, "sha256": ""}
    path = Path(path_value).expanduser()
    exists = path.exists() and path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "sha256": sha256(path.read_bytes()).hexdigest() if exists else "",
    }


def _command_record(command: Iterable[str] | str) -> list[str]:
    if isinstance(command, str):
        return [command]
    return [str(part) for part in command]


def _aeb0_dependency_record(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {"artifact_ref": "", "status": "missing", "artifact_sha256": ""}
    path = Path(path_value).expanduser()
    exists = path.exists() and path.is_file()
    return {
        "artifact_ref": str(path),
        "status": "present" if exists else "missing",
        "artifact_sha256": sha256(path.read_bytes()).hexdigest() if exists else "",
    }


def _sha256_json(payload: Any) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
