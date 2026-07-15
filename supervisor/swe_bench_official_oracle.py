"""Official SWE-bench oracle adapter for mergeability replay smoke runs."""
from __future__ import annotations

import ast
import base64
import inspect
import json
import math
import os
import platform as py_platform
import secrets
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit


SWE_BENCH_VERIFIER_EXECUTION_SPEC_SCHEMA_VERSION = (
    "supervisor-swe-bench-verifier-execution-spec/v1"
)
SWE_BENCH_BOUND_ORACLE_RECEIPT_SCHEMA_VERSION = (
    "supervisor-swe-bench-bound-oracle-receipt/v1"
)
SWE_BENCH_EXECUTION_AUTHORITY_SCHEMA_VERSION = (
    "supervisor-swe-bench-execution-authority/v1"
)
SWE_BENCH_STOCK_UNATTESTED_BACKEND_ID = (
    "supervisor.stock-swebench-python-cli/unattested-v1"
)
SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS = (
    "canonical_task_id",
    "dataset_name",
    "dataset_hash",
    "split",
    "split_hash",
    "instance_id",
    "instance_row_hash",
    "repository",
    "canonical_repo_id",
    "revision",
    "base_commit",
    "verifier_id",
    "verifier_version",
    "verifier_package",
    "verifier_hash",
    "container_image",
    "image_digest",
    "architecture",
    "os_name",
    "network_policy",
    "resource_limits",
    "harness_configuration",
)
SWE_BENCH_EXECUTION_AUTHORITY_OUTCOME_FIELDS = (
    "return_code",
    "oracle_unavailable",
    "patch_applied",
    "report_sha256",
    "report_instance_id",
    "resolved",
    "fail_to_pass_status",
    "pass_to_pass_status",
    "fail_to_pass_results_hash",
    "pass_to_pass_results_hash",
)


@dataclass(frozen=True)
class SweBenchVerifierExecutionSpec:
    """Immutable official-verifier authority derived from one TaskSpec.

    The serialized body includes the complete TaskSpec snapshot plus every
    official SWE-bench execution choice.  The nested values are recursively
    frozen so later mutation of caller-owned metadata cannot change verifier
    authority after construction.
    """

    _body: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        body = _plain_json_mapping(
            self._body,
            path="verifier_execution_spec",
        )
        _validate_execution_spec_body(body)
        object.__setattr__(self, "_body", _freeze_json(body))

    @classmethod
    def from_task_spec(
        cls,
        task_spec: Mapping[str, Any],
        *,
        task_spec_hash: str,
        verifier_version: str,
    ) -> "SweBenchVerifierExecutionSpec":
        snapshot = _plain_json_mapping(task_spec, path="task_spec")
        computed_task_spec_hash = _sha256_json(snapshot)
        if computed_task_spec_hash != str(task_spec_hash).strip().lower():
            raise ValueError(
                "TaskSpec snapshot hash changed while binding official verifier"
            )
        return cls(
            _build_execution_spec_body(
                snapshot,
                task_spec_hash=computed_task_spec_hash,
                verifier_version=verifier_version,
            )
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "SweBenchVerifierExecutionSpec":
        serialized = _plain_json_mapping(
            value,
            path="verifier_execution_spec",
        )
        observed_hash = str(
            serialized.pop("execution_spec_hash", "")
        ).strip().lower()
        if not observed_hash:
            raise ValueError(
                "verifier execution spec missing execution_spec_hash"
            )
        spec = cls(serialized)
        if observed_hash != spec.execution_spec_hash:
            raise ValueError("verifier execution spec hash mismatch")
        return spec

    @property
    def execution_spec_hash(self) -> str:
        return _sha256_json(self._body)

    @property
    def task_spec_hash(self) -> str:
        return str(self._body["task_spec_hash"])

    @property
    def task_spec(self) -> Mapping[str, Any]:
        return self._body["task_spec"]

    @property
    def canonical_task_id(self) -> str:
        return str(self._body["canonical_task_id"])

    @property
    def dataset_name(self) -> str:
        return str(self._body["dataset"]["name"])

    @property
    def dataset_hash(self) -> str:
        return str(self._body["dataset"]["hash"])

    @property
    def split(self) -> str:
        return str(self._body["split"]["name"])

    @property
    def split_hash(self) -> str:
        return str(self._body["split"]["hash"])

    @property
    def instance_id(self) -> str:
        return str(self._body["instance"]["id"])

    @property
    def instance_row(self) -> Mapping[str, Any]:
        return self._body["instance"]["row"]

    @property
    def instance_row_hash(self) -> str:
        return str(self._body["instance"]["row_hash"])

    @property
    def repository(self) -> str:
        return str(self._body["repository"]["repo"])

    @property
    def canonical_repo_id(self) -> str:
        return str(self._body["repository"]["canonical_repo_id"])

    @property
    def revision(self) -> str:
        return str(self._body["repository"]["revision"])

    @property
    def base_commit(self) -> str:
        return str(self._body["repository"]["base_commit"])

    @property
    def verifier_id(self) -> str:
        return str(self._body["verifier"]["id"])

    @property
    def verifier_version(self) -> str:
        return str(self._body["verifier"]["version"])

    @property
    def verifier_package(self) -> str:
        return str(self._body["verifier"]["package"])

    @property
    def verifier_hash(self) -> str:
        return str(self._body["verifier"]["hash"])

    @property
    def container_image(self) -> str:
        return str(self._body["container"]["image"])

    @property
    def image_digest(self) -> str:
        return str(self._body["container"]["digest"])

    @property
    def architecture(self) -> str:
        return str(self._body["platform"]["architecture"])

    @property
    def os_name(self) -> str:
        return str(self._body["platform"]["os_name"])

    @property
    def network_policy(self) -> str:
        return str(self._body["network_policy"])

    @property
    def resource_limits(self) -> Mapping[str, Any]:
        return self._body["resource_limits"]

    @property
    def harness(self) -> Mapping[str, Any]:
        return self._body["harness"]

    def execution_authority_pins(self) -> dict[str, Any]:
        """Return every value an execution backend must independently attest."""
        return {
            "canonical_task_id": self.canonical_task_id,
            "dataset_name": self.dataset_name,
            "dataset_hash": self.dataset_hash,
            "split": self.split,
            "split_hash": self.split_hash,
            "instance_id": self.instance_id,
            "instance_row_hash": self.instance_row_hash,
            "repository": self.repository,
            "canonical_repo_id": self.canonical_repo_id,
            "revision": self.revision,
            "base_commit": self.base_commit,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_package": self.verifier_package,
            "verifier_hash": self.verifier_hash,
            "container_image": self.container_image,
            "image_digest": self.image_digest,
            "architecture": self.architecture,
            "os_name": self.os_name,
            "network_policy": self.network_policy,
            "resource_limits": _thaw_json(self.resource_limits),
            "harness_configuration": _thaw_json(self.harness),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **_thaw_json(self._body),
            "execution_spec_hash": self.execution_spec_hash,
        }

    def context_binding(self) -> dict[str, Any]:
        """Return the redundant context mirrors the oracle must validate."""
        return {
            "task_spec": _thaw_json(self.task_spec),
            "task_spec_hash": self.task_spec_hash,
            "canonical_task_id": self.canonical_task_id,
            "dataset_name": self.dataset_name,
            "dataset_hash": self.dataset_hash,
            "split": self.split,
            "split_hash": self.split_hash,
            "instance_id": self.instance_id,
            "instance_row": _thaw_json(self.instance_row),
            "instance_row_hash": self.instance_row_hash,
            "repo": self.repository,
            "canonical_repo_id": self.canonical_repo_id,
            "revision": self.revision,
            "base_commit": self.base_commit,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_package": self.verifier_package,
            "verifier_hash": self.verifier_hash,
            "container_image": self.container_image,
            "image_digest": self.image_digest,
            "architecture": self.architecture,
            "os_name": self.os_name,
            "network_policy": self.network_policy,
            "resource_limits": _thaw_json(self.resource_limits),
            "harness": _thaw_json(self.harness),
            "verifier_execution_spec": self.to_dict(),
            "verifier_execution_spec_hash": self.execution_spec_hash,
        }


def new_swe_bench_verification_nonce() -> str:
    """Return a fresh verifier-owned nonce for one backend execution request."""
    return secrets.token_hex(32)


def build_swe_bench_execution_authority(
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
    mode: str,
    backend_id: str,
    backend_manifest_hash: str,
    candidate_id: str,
    model_patch_sha256: str,
    producer_run_result_hash: str,
    request_nonce: str,
    backend_run_id: str,
    observed_pins: Mapping[str, Any] | None,
    pin_evidence: Mapping[str, Mapping[str, Any]] | None,
    outcome: Mapping[str, Any],
    signer: Any | None = None,
) -> dict[str, Any]:
    """Build a signed, hash-bound execution-backend authority record.

    The builder only canonicalizes a backend's statement. Authenticity comes
    from a verifier-configured trust root, not from the hash or this helper.
    """
    if not isinstance(execution_spec, SweBenchVerifierExecutionSpec):
        raise ValueError(
            "execution authority requires a SweBenchVerifierExecutionSpec"
        )
    normalized_mode = str(mode).strip()
    if normalized_mode not in {"operational", "fixture", "unattested"}:
        raise ValueError("execution authority mode is invalid")
    normalized_backend_id = str(backend_id).strip()
    normalized_backend_run_id = str(backend_run_id).strip()
    if not normalized_backend_id:
        raise ValueError("execution authority backend_id must be non-empty")
    if not normalized_backend_run_id:
        raise ValueError(
            "execution authority backend_run_id must be non-empty"
        )
    normalized_backend_manifest_hash = _canonical_sha256(
        backend_manifest_hash,
        path="execution_authority.backend_manifest_hash",
    )
    normalized_candidate_id = _canonical_sha256(
        candidate_id,
        path="execution_authority.candidate_id",
    )
    normalized_model_patch_sha256 = _canonical_sha256(
        model_patch_sha256,
        path="execution_authority.model_patch_sha256",
    )
    normalized_producer_hash = _canonical_sha256(
        producer_run_result_hash,
        path="execution_authority.producer_run_result_hash",
    )
    normalized_nonce = _canonical_sha256(
        request_nonce,
        path="execution_authority.request_nonce",
    )

    expected_pins = execution_spec.execution_authority_pins()
    observed = dict(observed_pins or {})
    evidence = dict(pin_evidence or {})
    pin_records: dict[str, dict[str, Any]] = {}
    unmet_pins: list[str] = []
    for pin in SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS:
        expected_value = _plain_json(
            expected_pins[pin],
            path=f"execution_authority.pins.{pin}.expected",
        )
        observed_value = _plain_json(
            observed.get(pin),
            path=f"execution_authority.pins.{pin}.observed",
        )
        raw_evidence = evidence.get(pin, {})
        evidence_value = _canonical_execution_pin_evidence(
            raw_evidence,
            pin=pin,
        )
        enforced = (
            pin in observed
            and observed_value == expected_value
            and evidence_value is not None
        )
        if not enforced:
            unmet_pins.append(pin)
        pin_records[pin] = {
            "expected": expected_value,
            "observed": observed_value,
            "enforced": enforced,
            "evidence": evidence_value,
        }

    canonical_outcome = _canonical_execution_authority_outcome(
        outcome,
        execution_spec=execution_spec,
    )
    body = {
        "schema_version": SWE_BENCH_EXECUTION_AUTHORITY_SCHEMA_VERSION,
        "attestation_source": "execution_backend",
        "mode": normalized_mode,
        "backend_id": normalized_backend_id,
        "backend_manifest_hash": normalized_backend_manifest_hash,
        "backend_run_id": normalized_backend_run_id,
        "execution_spec_hash": execution_spec.execution_spec_hash,
        "task_spec_hash": execution_spec.task_spec_hash,
        "canonical_task_id": execution_spec.canonical_task_id,
        "candidate_id": normalized_candidate_id,
        "model_patch_sha256": normalized_model_patch_sha256,
        "producer_run_result_hash": normalized_producer_hash,
        "request_nonce": normalized_nonce,
        "pins": pin_records,
        "unmet_pins": unmet_pins,
        "enforced": not unmet_pins,
        "outcome": canonical_outcome,
    }
    payload = _canonical_json_bytes(body)
    signature = (
        _sign_execution_authority(signer, payload)
        if signer is not None
        else None
    )
    if normalized_mode == "operational" and signature is None:
        raise ValueError(
            "operational execution authority requires a backend signature"
        )
    return {
        **body,
        "authority_hash": sha256(payload).hexdigest(),
        "signature": signature,
    }


def validate_swe_bench_execution_authority(
    value: Mapping[str, Any],
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
    candidate_id: str,
    model_patch_sha256: str,
    producer_run_result_hash: str,
    request_nonce: str,
    oracle_result: Mapping[str, Any],
    oracle_receipt: Mapping[str, Any],
    require_enforced: bool,
    authority_verifier: Any | None,
    trusted_backend_manifest_hashes: Sequence[str],
) -> dict[str, Any]:
    """Validate backend authenticity, candidate binding, pins, and outcome."""
    serialized = _plain_json_mapping(
        value,
        path="execution_authority",
    )
    observed_hash = str(serialized.pop("authority_hash", "")).strip().casefold()
    signature = serialized.pop("signature", None)
    if not observed_hash:
        raise ValueError("execution authority missing authority_hash")
    payload = _canonical_json_bytes(serialized)
    if observed_hash != sha256(payload).hexdigest():
        raise ValueError("execution authority hash mismatch")
    _require_exact_keys(
        serialized,
        {
            "schema_version",
            "attestation_source",
            "mode",
            "backend_id",
            "backend_manifest_hash",
            "backend_run_id",
            "execution_spec_hash",
            "task_spec_hash",
            "canonical_task_id",
            "candidate_id",
            "model_patch_sha256",
            "producer_run_result_hash",
            "request_nonce",
            "pins",
            "unmet_pins",
            "enforced",
            "outcome",
        },
        path="execution_authority",
    )
    if serialized["schema_version"] != (
        SWE_BENCH_EXECUTION_AUTHORITY_SCHEMA_VERSION
    ):
        raise ValueError("execution authority schema version is invalid")
    if serialized["attestation_source"] != "execution_backend":
        raise ValueError(
            "execution authority must originate from an execution backend"
        )
    mode = str(serialized["mode"])
    if mode not in {"operational", "fixture", "unattested"}:
        raise ValueError("execution authority mode is invalid")
    if not str(serialized["backend_id"]).strip():
        raise ValueError("execution authority backend_id must be non-empty")
    if not str(serialized["backend_run_id"]).strip():
        raise ValueError(
            "execution authority backend_run_id must be non-empty"
        )
    backend_manifest_hash = _canonical_sha256(
        str(serialized["backend_manifest_hash"]),
        path="execution_authority.backend_manifest_hash",
    )
    if serialized["execution_spec_hash"] != execution_spec.execution_spec_hash:
        raise ValueError("execution authority execution_spec_hash mismatch")
    if serialized["task_spec_hash"] != execution_spec.task_spec_hash:
        raise ValueError("execution authority task_spec_hash mismatch")
    if serialized["canonical_task_id"] != execution_spec.canonical_task_id:
        raise ValueError("execution authority canonical_task_id mismatch")
    expected_bindings = {
        "candidate_id": candidate_id,
        "model_patch_sha256": model_patch_sha256,
        "producer_run_result_hash": producer_run_result_hash,
        "request_nonce": request_nonce,
    }
    for key, expected in expected_bindings.items():
        if _canonical_sha256(
            str(serialized[key]),
            path=f"execution_authority.{key}",
        ) != _canonical_sha256(
            expected,
            path=f"expected_execution_authority.{key}",
        ):
            raise ValueError(f"execution authority {key} mismatch")

    raw_pins = serialized["pins"]
    if not isinstance(raw_pins, Mapping):
        raise ValueError("execution authority pins must be a mapping")
    if set(raw_pins) != set(SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS):
        raise ValueError(
            "execution authority pins must exactly cover every required pin"
        )
    expected_pins = execution_spec.execution_authority_pins()
    computed_unmet: list[str] = []
    for pin in SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS:
        raw_record = raw_pins[pin]
        if not isinstance(raw_record, Mapping):
            raise ValueError(
                f"execution authority pin {pin} must be a mapping"
            )
        record = _plain_json_mapping(
            raw_record,
            path=f"execution_authority.pins.{pin}",
        )
        _require_exact_keys(
            record,
            {"expected", "observed", "enforced", "evidence"},
            path=f"execution_authority.pins.{pin}",
        )
        expected_value = _plain_json(
            expected_pins[pin],
            path=f"expected_execution_authority.pins.{pin}",
        )
        if record["expected"] != expected_value:
            raise ValueError(
                f"execution authority pin {pin} expected value mismatch"
            )
        enforced = record["enforced"]
        if not isinstance(enforced, bool):
            raise ValueError(
                f"execution authority pin {pin} enforced must be a bool"
            )
        evidence = record["evidence"]
        if evidence is not None:
            _canonical_execution_pin_evidence(evidence, pin=pin)
        if enforced and record["observed"] != expected_value:
            raise ValueError(
                f"execution authority pin {pin} observed value mismatch"
            )
        if enforced and evidence is None:
            raise ValueError(
                f"execution authority pin {pin} lacks backend evidence"
            )
        if not enforced:
            computed_unmet.append(pin)

    raw_unmet = serialized["unmet_pins"]
    if (
        not isinstance(raw_unmet, list)
        or any(not isinstance(pin, str) for pin in raw_unmet)
        or raw_unmet != computed_unmet
    ):
        raise ValueError("execution authority unmet_pins mismatch")
    enforced = serialized["enforced"]
    if not isinstance(enforced, bool) or enforced is not (not computed_unmet):
        raise ValueError("execution authority enforced summary mismatch")
    outcome = _canonical_execution_authority_outcome(
        serialized["outcome"],
        execution_spec=execution_spec,
    )
    _validate_execution_authority_outcome_binding(
        outcome,
        oracle_result=oracle_result,
        oracle_receipt=oracle_receipt,
        execution_spec=execution_spec,
        require_available=require_enforced,
    )

    if mode == "operational":
        trusted_hashes = {
            _canonical_sha256(
                value,
                path="trusted_backend_manifest_hash",
            )
            for value in trusted_backend_manifest_hashes
        }
        if backend_manifest_hash not in trusted_hashes:
            raise ValueError(
                "execution authority backend manifest is not trusted"
            )
        if authority_verifier is None:
            raise ValueError(
                "execution authority has no configured trust verifier"
            )
        _verify_execution_authority_signature(
            authority_verifier,
            payload,
            signature,
        )
    if require_enforced:
        if mode != "operational":
            raise ValueError(
                "available official grade requires operational execution "
                "authority"
            )
        if computed_unmet:
            raise ValueError(
                "execution authority required pin is not enforced: "
                f"{computed_unmet[0]}"
            )

    return {
        **serialized,
        "authority_hash": observed_hash,
        "signature": signature,
    }


def _canonical_execution_pin_evidence(
    value: Any,
    *,
    pin: str,
) -> dict[str, Any] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            f"execution authority pin {pin} evidence must be a mapping"
        )
    evidence = _plain_json_mapping(
        value,
        path=f"execution_authority.pins.{pin}.evidence",
    )
    _require_exact_keys(
        evidence,
        {"kind", "ref", "sha256"},
        path=f"execution_authority.pins.{pin}.evidence",
    )
    if not str(evidence["kind"]).strip() or not str(evidence["ref"]).strip():
        raise ValueError(
            f"execution authority pin {pin} evidence identity is invalid"
        )
    evidence["sha256"] = _canonical_sha256(
        str(evidence["sha256"]),
        path=f"execution_authority.pins.{pin}.evidence.sha256",
    )
    return evidence


def _canonical_execution_authority_outcome(
    value: Any,
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("execution authority outcome must be a mapping")
    outcome = _plain_json_mapping(
        value,
        path="execution_authority.outcome",
    )
    _require_exact_keys(
        outcome,
        {
            "return_code",
            "oracle_unavailable",
            "patch_applied",
            "report_sha256",
            "report_instance_id",
            "resolved",
            "fail_to_pass_status",
            "pass_to_pass_status",
            "fail_to_pass_results_hash",
            "pass_to_pass_results_hash",
        },
        path="execution_authority.outcome",
    )
    if isinstance(outcome["return_code"], bool) or not isinstance(
        outcome["return_code"],
        int,
    ):
        raise ValueError(
            "execution authority outcome return_code must be an integer"
        )
    for key in ("oracle_unavailable", "patch_applied", "resolved"):
        if not isinstance(outcome[key], bool):
            raise ValueError(
                f"execution authority outcome {key} must be a bool"
            )
    for key in ("fail_to_pass_status", "pass_to_pass_status"):
        if outcome[key] not in {"pass", "fail", "unavailable"}:
            raise ValueError(
                f"execution authority outcome {key} is invalid"
            )
    for key in (
        "report_sha256",
        "fail_to_pass_results_hash",
        "pass_to_pass_results_hash",
    ):
        raw = outcome[key]
        outcome[key] = (
            None
            if raw is None
            else _canonical_sha256(
                str(raw),
                path=f"execution_authority.outcome.{key}",
            )
        )
    report_instance_id = str(outcome["report_instance_id"]).strip()
    if report_instance_id and report_instance_id != execution_spec.instance_id:
        raise ValueError(
            "execution authority outcome report_instance_id mismatch"
        )
    outcome["report_instance_id"] = report_instance_id
    return outcome


def _validate_execution_authority_outcome_binding(
    outcome: Mapping[str, Any],
    *,
    oracle_result: Mapping[str, Any],
    oracle_receipt: Mapping[str, Any],
    execution_spec: SweBenchVerifierExecutionSpec,
    require_available: bool,
) -> None:
    result_outcome = _canonical_execution_outcome_projection(
        oracle_result,
        execution_spec=execution_spec,
        path="official SWE-bench oracle result",
    )
    receipt_outcome = _canonical_execution_outcome_projection(
        oracle_receipt,
        execution_spec=execution_spec,
        path="official SWE-bench oracle receipt",
    )
    if outcome != result_outcome or outcome != receipt_outcome:
        raise ValueError(
            "execution authority outcome differs from oracle result or "
            "receipt"
        )
    unavailable = bool(outcome["oracle_unavailable"])
    expected_resolved = (
        outcome["fail_to_pass_status"] == "pass"
        and outcome["pass_to_pass_status"] == "pass"
    )
    if outcome["resolved"] is not expected_resolved:
        raise ValueError(
            "execution authority resolved status contradicts test outcomes"
        )
    if not require_available:
        return
    if (
        unavailable
        or outcome["return_code"] != 0
        or outcome["patch_applied"] is not True
        or outcome["report_sha256"] is None
        or not outcome["report_instance_id"]
        or outcome["fail_to_pass_status"] == "unavailable"
        or outcome["pass_to_pass_status"] == "unavailable"
        or outcome["fail_to_pass_results_hash"] is None
        or outcome["pass_to_pass_results_hash"] is None
    ):
        raise ValueError(
            "available official grade lacks a complete backend outcome"
        )


def _canonical_execution_outcome_projection(
    value: Mapping[str, Any],
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
    path: str,
) -> dict[str, Any]:
    missing = [
        field
        for field in SWE_BENCH_EXECUTION_AUTHORITY_OUTCOME_FIELDS
        if field not in value
    ]
    if missing:
        raise ValueError(
            f"{path} missing signed outcome field {missing[0]}"
        )
    return _canonical_execution_authority_outcome(
        {
            field: value[field]
            for field in SWE_BENCH_EXECUTION_AUTHORITY_OUTCOME_FIELDS
        },
        execution_spec=execution_spec,
    )


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sign_execution_authority(
    signer: Any,
    payload: bytes,
) -> dict[str, str]:
    key_id = str(getattr(signer, "key_id", "") or "").strip()
    algorithm = str(getattr(signer, "algorithm", "") or "").strip()
    if not key_id or not algorithm:
        raise ValueError(
            "execution authority signer requires key_id and algorithm"
        )
    sign = getattr(signer, "sign", None)
    result = sign(payload) if callable(sign) else signer(payload)
    if isinstance(result, Mapping):
        signature = {
            "key_id": str(result.get("key_id") or key_id),
            "algorithm": str(result.get("algorithm") or algorithm),
            "signature": str(
                result.get("signature") or result.get("value") or ""
            ),
        }
    elif isinstance(result, bytes):
        signature = {
            "key_id": key_id,
            "algorithm": algorithm,
            "signature": base64.b64encode(result).decode("ascii"),
        }
    elif isinstance(result, str):
        signature = {
            "key_id": key_id,
            "algorithm": algorithm,
            "signature": result,
        }
    else:
        raise TypeError(
            "execution authority signer must return mapping, bytes, or text"
        )
    if (
        signature["key_id"] != key_id
        or signature["algorithm"] != algorithm
        or not signature["signature"]
    ):
        raise ValueError(
            "execution authority signer returned invalid signature metadata"
        )
    return signature


def _verify_execution_authority_signature(
    verifier: Any,
    payload: bytes,
    signature: Any,
) -> None:
    if not isinstance(signature, Mapping) or set(signature) != {
        "key_id",
        "algorithm",
        "signature",
    }:
        raise ValueError("execution authority signature is invalid")
    verify = getattr(verifier, "verify", None)
    try:
        valid = (
            verify(payload, signature)
            if callable(verify)
            else verifier(payload, signature)
        )
    except Exception as exc:
        raise ValueError(
            "execution authority signature verification failed"
        ) from exc
    if inspect.isawaitable(valid):
        close = getattr(valid, "close", None)
        if callable(close):
            close()
        raise ValueError(
            "execution authority signature verifier must be synchronous"
        )
    if valid is not True:
        raise ValueError("execution authority signature is not trusted")


def _build_execution_spec_body(
    task_spec: Mapping[str, Any],
    *,
    task_spec_hash: str,
    verifier_version: str,
) -> dict[str, Any]:
    required_task_text = {
        key: _required_mapping_text(task_spec, key, path="task_spec")
        for key in (
            "task_id",
            "task_family",
            "repo",
            "revision",
            "dataset_hash",
            "split_hash",
            "problem_statement",
            "image_digest",
            "architecture",
            "os_name",
            "network_policy",
            "verifier_id",
            "verifier_hash",
            "canonical_task_key",
            "task_class",
            "canonical_repo_id",
        )
    }
    if "swebench" not in required_task_text["task_family"].casefold():
        raise ValueError(
            "official SWE-bench verifier requires a SWE-bench TaskSpec family"
        )
    revision = _canonical_git_commit(
        required_task_text["revision"],
        path="task_spec.revision",
    )
    dataset_hash = _canonical_sha256(
        required_task_text["dataset_hash"],
        path="task_spec.dataset_hash",
    )
    split_hash = _canonical_sha256(
        required_task_text["split_hash"],
        path="task_spec.split_hash",
    )
    verifier_hash = _canonical_sha256(
        required_task_text["verifier_hash"],
        path="task_spec.verifier_hash",
    )
    image_digest = _canonical_image_digest(
        required_task_text["image_digest"],
        path="task_spec.image_digest",
    )
    if required_task_text["verifier_id"] != "official-swebench":
        raise ValueError(
            "official SWE-bench TaskSpec verifier_id must be official-swebench"
        )
    network_policy = required_task_text["network_policy"]
    if network_policy not in {"disabled", "restricted", "enabled"}:
        raise ValueError("official SWE-bench TaskSpec network_policy is invalid")

    resource_limits = _required_mapping(
        task_spec,
        "resource_limits",
        path="task_spec",
    )
    metadata = _required_mapping(task_spec, "metadata", path="task_spec")
    official = _required_mapping(
        task_spec,
        "verifier_execution",
        path="task_spec",
    )
    _require_exact_keys(
        official,
        {
            "dataset",
            "split",
            "instance",
            "repository",
            "verifier",
            "container",
            "platform",
            "network_policy",
            "resource_limits",
            "harness",
        },
        path="task_spec.verifier_execution",
    )

    dataset = _required_mapping(
        official,
        "dataset",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(dataset, {"name", "hash"}, path="verifier_execution.dataset")
    dataset_name = _required_mapping_text(
        dataset,
        "name",
        path="verifier_execution.dataset",
    )
    if _canonical_sha256(
        _required_mapping_text(
            dataset,
            "hash",
            path="verifier_execution.dataset",
        ),
        path="verifier_execution.dataset.hash",
    ) != dataset_hash:
        raise ValueError(
            "official SWE-bench dataset hash does not match TaskSpec"
        )

    split = _required_mapping(
        official,
        "split",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(split, {"name", "hash"}, path="verifier_execution.split")
    split_name = _required_mapping_text(
        split,
        "name",
        path="verifier_execution.split",
    )
    if _canonical_sha256(
        _required_mapping_text(
            split,
            "hash",
            path="verifier_execution.split",
        ),
        path="verifier_execution.split.hash",
    ) != split_hash:
        raise ValueError("official SWE-bench split hash does not match TaskSpec")

    instance = _required_mapping(
        official,
        "instance",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        instance,
        {"id", "row", "row_hash"},
        path="verifier_execution.instance",
    )
    instance_id = _required_mapping_text(
        instance,
        "id",
        path="verifier_execution.instance",
    )
    metadata_instance_id = _required_mapping_text(
        metadata,
        "instance_id",
        path="task_spec.metadata",
    )
    if instance_id != metadata_instance_id:
        raise ValueError(
            "official SWE-bench instance id does not match TaskSpec metadata"
        )
    if _normalize_task_key(instance_id) != required_task_text["canonical_task_key"]:
        raise ValueError(
            "official SWE-bench instance id does not match canonical task key"
        )
    instance_row = _required_mapping(
        instance,
        "row",
        path="verifier_execution.instance",
    )
    computed_row_hash = _sha256_json(instance_row)
    observed_row_hash = _canonical_sha256(
        _required_mapping_text(
            instance,
            "row_hash",
            path="verifier_execution.instance",
        ),
        path="verifier_execution.instance.row_hash",
    )
    if observed_row_hash != computed_row_hash:
        raise ValueError("official SWE-bench instance row hash mismatch")
    row_instance_id = _required_mapping_text(
        instance_row,
        "instance_id",
        path="verifier_execution.instance.row",
    )
    if row_instance_id != instance_id:
        raise ValueError(
            "official SWE-bench instance row id does not match TaskSpec"
        )
    row_repository = _required_mapping_text(
        instance_row,
        "repo",
        path="verifier_execution.instance.row",
    )
    if not _repository_ids_match(
        row_repository,
        required_task_text["canonical_repo_id"],
    ):
        raise ValueError(
            "official SWE-bench instance row repository does not match TaskSpec"
        )
    row_base_commit = _canonical_git_commit(
        _required_mapping_text(
            instance_row,
            "base_commit",
            path="verifier_execution.instance.row",
        ),
        path="verifier_execution.instance.row.base_commit",
    )
    if row_base_commit != revision:
        raise ValueError(
            "official SWE-bench instance row base_commit does not match TaskSpec"
        )
    if _required_mapping_text(
        instance_row,
        "problem_statement",
        path="verifier_execution.instance.row",
    ) != required_task_text["problem_statement"]:
        raise ValueError(
            "official SWE-bench instance row problem_statement does not match TaskSpec"
        )
    _validate_optional_row_pin(
        instance_row,
        keys=("dataset_name", "dataset"),
        expected=dataset_name,
        label="dataset identity",
    )
    _validate_optional_row_pin(
        instance_row,
        keys=("dataset_hash",),
        expected=dataset_hash,
        label="dataset hash",
        normalizer=lambda value: _canonical_sha256(
            value,
            path="verifier_execution.instance.row.dataset_hash",
        ),
    )
    _validate_optional_row_pin(
        instance_row,
        keys=("split", "dataset_split"),
        expected=split_name,
        label="split",
    )
    _validate_optional_row_pin(
        instance_row,
        keys=("split_hash",),
        expected=split_hash,
        label="split hash",
        normalizer=lambda value: _canonical_sha256(
            value,
            path="verifier_execution.instance.row.split_hash",
        ),
    )

    repository = _required_mapping(
        official,
        "repository",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        repository,
        {"repo", "canonical_repo_id", "revision", "base_commit"},
        path="verifier_execution.repository",
    )
    if _required_mapping_text(
        repository,
        "repo",
        path="verifier_execution.repository",
    ) != required_task_text["repo"]:
        raise ValueError(
            "official SWE-bench repository does not match TaskSpec repo"
        )
    if _required_mapping_text(
        repository,
        "canonical_repo_id",
        path="verifier_execution.repository",
    ) != required_task_text["canonical_repo_id"]:
        raise ValueError(
            "official SWE-bench canonical repository does not match TaskSpec"
        )
    if _canonical_git_commit(
        _required_mapping_text(
            repository,
            "revision",
            path="verifier_execution.repository",
        ),
        path="verifier_execution.repository.revision",
    ) != revision:
        raise ValueError(
            "official SWE-bench repository revision does not match TaskSpec"
        )
    if _canonical_git_commit(
        _required_mapping_text(
            repository,
            "base_commit",
            path="verifier_execution.repository",
        ),
        path="verifier_execution.repository.base_commit",
    ) != revision:
        raise ValueError(
            "official SWE-bench repository base_commit does not match TaskSpec"
        )

    verifier = _required_mapping(
        official,
        "verifier",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        verifier,
        {"id", "version", "package", "hash"},
        path="verifier_execution.verifier",
    )
    bound_verifier_version = str(verifier_version).strip()
    if not bound_verifier_version:
        raise ValueError("official SWE-bench verifier_version must be non-empty")
    if _required_mapping_text(
        verifier,
        "id",
        path="verifier_execution.verifier",
    ) != required_task_text["verifier_id"]:
        raise ValueError(
            "official SWE-bench verifier id does not match TaskSpec"
        )
    if _required_mapping_text(
        verifier,
        "version",
        path="verifier_execution.verifier",
    ) != bound_verifier_version:
        raise ValueError(
            "official SWE-bench verifier version does not match bound adapter"
        )
    verifier_package = _required_mapping_text(
        verifier,
        "package",
        path="verifier_execution.verifier",
    )
    package_name, separator, package_version = verifier_package.partition("==")
    if package_name.strip().casefold() != "swebench":
        raise ValueError(
            "official SWE-bench verifier package must name swebench"
        )
    if separator and package_version.strip() != bound_verifier_version:
        raise ValueError(
            "official SWE-bench verifier package version does not match "
            "bound adapter"
        )
    if _canonical_sha256(
        _required_mapping_text(
            verifier,
            "hash",
            path="verifier_execution.verifier",
        ),
        path="verifier_execution.verifier.hash",
    ) != verifier_hash:
        raise ValueError(
            "official SWE-bench verifier package hash does not match TaskSpec"
        )

    container = _required_mapping(
        official,
        "container",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        container,
        {"image", "digest"},
        path="verifier_execution.container",
    )
    container_image = _required_mapping_text(
        container,
        "image",
        path="verifier_execution.container",
    )
    container_digest = _canonical_image_digest(
        _required_mapping_text(
            container,
            "digest",
            path="verifier_execution.container",
        ),
        path="verifier_execution.container.digest",
    )
    if container_digest != image_digest:
        raise ValueError(
            "official SWE-bench container digest does not match TaskSpec"
        )
    if "@sha256:" not in container_image.casefold():
        raise ValueError(
            "official SWE-bench container image must be digest-pinned"
        )
    embedded_digest = "sha256:" + container_image.casefold().rsplit(
        "@sha256:",
        1,
    )[1]
    if _canonical_image_digest(
        embedded_digest,
        path="verifier_execution.container.image",
    ) != image_digest:
        raise ValueError(
            "official SWE-bench container image digest does not match TaskSpec"
        )

    platform = _required_mapping(
        official,
        "platform",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        platform,
        {"architecture", "os_name"},
        path="verifier_execution.platform",
    )
    if _required_mapping_text(
        platform,
        "architecture",
        path="verifier_execution.platform",
    ) != required_task_text["architecture"]:
        raise ValueError(
            "official SWE-bench platform architecture does not match TaskSpec"
        )
    if _required_mapping_text(
        platform,
        "os_name",
        path="verifier_execution.platform",
    ) != required_task_text["os_name"]:
        raise ValueError(
            "official SWE-bench platform OS does not match TaskSpec"
        )
    if _required_mapping_text(
        official,
        "network_policy",
        path="task_spec.verifier_execution",
    ) != network_policy:
        raise ValueError(
            "official SWE-bench network policy does not match TaskSpec"
        )
    official_resource_limits = _required_mapping(
        official,
        "resource_limits",
        path="task_spec.verifier_execution",
    )
    if _sha256_json(official_resource_limits) != _sha256_json(resource_limits):
        raise ValueError(
            "official SWE-bench resource limits do not match TaskSpec"
        )

    harness = _required_mapping(
        official,
        "harness",
        path="task_spec.verifier_execution",
    )
    _require_exact_keys(
        harness,
        {
            "namespace",
            "cache_level",
            "clean",
            "max_workers",
            "timeout_s",
            "subprocess_timeout_s",
            "model_name",
            "run_id_prefix",
        },
        path="verifier_execution.harness",
    )
    canonical_harness = {
        "namespace": _required_mapping_text(
            harness,
            "namespace",
            path="verifier_execution.harness",
        ),
        "cache_level": _required_mapping_text(
            harness,
            "cache_level",
            path="verifier_execution.harness",
        ),
        "clean": _required_bool(
            harness,
            "clean",
            path="verifier_execution.harness",
        ),
        "max_workers": _required_positive_int(
            harness,
            "max_workers",
            path="verifier_execution.harness",
        ),
        "timeout_s": _required_positive_int(
            harness,
            "timeout_s",
            path="verifier_execution.harness",
        ),
        "subprocess_timeout_s": _required_positive_int(
            harness,
            "subprocess_timeout_s",
            path="verifier_execution.harness",
        ),
        "model_name": _required_mapping_text(
            harness,
            "model_name",
            path="verifier_execution.harness",
        ),
        "run_id_prefix": _required_mapping_text(
            harness,
            "run_id_prefix",
            path="verifier_execution.harness",
        ),
    }
    if canonical_harness["cache_level"] not in {
        "none",
        "base",
        "env",
        "instance",
    }:
        raise ValueError("official SWE-bench harness cache_level is invalid")
    for key in ("max_workers", "timeout_s", "subprocess_timeout_s"):
        if key not in resource_limits or resource_limits[key] != canonical_harness[key]:
            raise ValueError(
                f"official SWE-bench harness {key} does not match "
                "TaskSpec resource limits"
            )

    canonical_task_id = _canonical_task_identity(task_spec)
    return {
        "schema_version": SWE_BENCH_VERIFIER_EXECUTION_SPEC_SCHEMA_VERSION,
        "task_spec": _plain_json_mapping(task_spec, path="task_spec"),
        "task_spec_hash": task_spec_hash,
        "canonical_task_id": canonical_task_id,
        "dataset": {
            "name": dataset_name,
            "hash": dataset_hash,
        },
        "split": {
            "name": split_name,
            "hash": split_hash,
        },
        "instance": {
            "id": instance_id,
            "row": _plain_json_mapping(
                instance_row,
                path="verifier_execution.instance.row",
            ),
            "row_hash": computed_row_hash,
        },
        "repository": {
            "repo": required_task_text["repo"],
            "canonical_repo_id": required_task_text["canonical_repo_id"],
            "revision": revision,
            "base_commit": revision,
        },
        "verifier": {
            "id": required_task_text["verifier_id"],
            "version": bound_verifier_version,
            "package": verifier_package,
            "hash": verifier_hash,
        },
        "container": {
            "image": container_image,
            "digest": image_digest,
        },
        "platform": {
            "architecture": required_task_text["architecture"],
            "os_name": required_task_text["os_name"],
        },
        "network_policy": network_policy,
        "resource_limits": _plain_json_mapping(
            resource_limits,
            path="task_spec.resource_limits",
        ),
        "harness": canonical_harness,
    }


def _validate_execution_spec_body(body: Mapping[str, Any]) -> None:
    _require_exact_keys(
        body,
        {
            "schema_version",
            "task_spec",
            "task_spec_hash",
            "canonical_task_id",
            "dataset",
            "split",
            "instance",
            "repository",
            "verifier",
            "container",
            "platform",
            "network_policy",
            "resource_limits",
            "harness",
        },
        path="verifier_execution_spec",
    )
    if body.get("schema_version") != (
        SWE_BENCH_VERIFIER_EXECUTION_SPEC_SCHEMA_VERSION
    ):
        raise ValueError("verifier execution spec schema version is invalid")
    task_spec = _required_mapping(
        body,
        "task_spec",
        path="verifier_execution_spec",
    )
    task_spec_hash = _canonical_sha256(
        _required_mapping_text(
            body,
            "task_spec_hash",
            path="verifier_execution_spec",
        ),
        path="verifier_execution_spec.task_spec_hash",
    )
    if _sha256_json(task_spec) != task_spec_hash:
        raise ValueError("verifier execution spec TaskSpec hash mismatch")
    verifier = _required_mapping(
        body,
        "verifier",
        path="verifier_execution_spec",
    )
    expected = _build_execution_spec_body(
        task_spec,
        task_spec_hash=task_spec_hash,
        verifier_version=_required_mapping_text(
            verifier,
            "version",
            path="verifier_execution_spec.verifier",
        ),
    )
    if _sha256_json(body) != _sha256_json(expected):
        raise ValueError(
            "verifier execution spec does not match its complete TaskSpec"
        )


def _canonical_task_identity(task_spec: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": "supervisor-canonical-task-identity/v1",
        "canonical_repo_id": _required_mapping_text(
            task_spec,
            "canonical_repo_id",
            path="task_spec",
        ),
        "revision": _canonical_git_commit(
            _required_mapping_text(
                task_spec,
                "revision",
                path="task_spec",
            ),
            path="task_spec.revision",
        ),
        "dataset_hash": _canonical_sha256(
            _required_mapping_text(
                task_spec,
                "dataset_hash",
                path="task_spec",
            ),
            path="task_spec.dataset_hash",
        ),
        "split_hash": _canonical_sha256(
            _required_mapping_text(
                task_spec,
                "split_hash",
                path="task_spec",
            ),
            path="task_spec.split_hash",
        ),
        "canonical_task_key": _normalize_task_key(
            _required_mapping_text(
                task_spec,
                "canonical_task_key",
                path="task_spec",
            )
        ),
    }
    return _sha256_json(payload)


def _validate_optional_row_pin(
    row: Mapping[str, Any],
    *,
    keys: Sequence[str],
    expected: str,
    label: str,
    normalizer: Any | None = None,
) -> None:
    observed: list[str] = []
    for key in keys:
        if key not in row:
            continue
        raw = row[key]
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"official SWE-bench instance row {label} must be non-empty"
            )
        observed.append(normalizer(raw) if normalizer else raw.strip())
    if any(value != expected for value in observed):
        raise ValueError(
            f"official SWE-bench instance row {label} does not match TaskSpec"
        )


def _plain_json_mapping(
    value: Mapping[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    plain = _plain_json(value, path=path)
    if not isinstance(plain, dict):
        raise ValueError(f"{path} must be a mapping")
    return plain


def _plain_json(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise ValueError(f"{path} keys must be non-empty strings")
            plain[raw_key] = _plain_json(
                child,
                path=f"{path}.{raw_key}",
            )
        return plain
    if isinstance(value, (list, tuple)):
        return [
            _plain_json(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite floats")
        return value
    raise ValueError(f"{path} contains unsupported value {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _freeze_json(child)
            for key, child in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(child)
            for key, child in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _sha256_json(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
    *,
    path: str,
) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, Mapping):
        raise ValueError(f"{path}.{key} must be a mapping")
    return _plain_json_mapping(child, path=f"{path}.{key}")


def _required_mapping_text(
    value: Mapping[str, Any],
    key: str,
    *,
    path: str,
) -> str:
    child = value.get(key)
    if not isinstance(child, str) or not child.strip():
        raise ValueError(f"{path}.{key} must be non-empty text")
    return child.strip()


def _required_bool(
    value: Mapping[str, Any],
    key: str,
    *,
    path: str,
) -> bool:
    child = value.get(key)
    if not isinstance(child, bool):
        raise ValueError(f"{path}.{key} must be a bool")
    return child


def _required_positive_int(
    value: Mapping[str, Any],
    key: str,
    *,
    path: str,
) -> int:
    child = value.get(key)
    if isinstance(child, bool) or not isinstance(child, int) or child <= 0:
        raise ValueError(f"{path}.{key} must be a positive integer")
    return child


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    path: str,
) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise ValueError(
            f"{path} keys are invalid" + (": " + ";".join(detail) if detail else "")
        )


def _canonical_sha256(value: str, *, path: str) -> str:
    raw = str(value).strip().casefold().removeprefix("sha256:")
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"{path} must be a sha256 digest")
    return raw


def _canonical_image_digest(value: str, *, path: str) -> str:
    raw = str(value).strip().casefold()
    if not raw.startswith("sha256:"):
        raise ValueError(f"{path} must be a sha256-pinned image digest")
    return "sha256:" + _canonical_sha256(raw, path=path)


def _canonical_git_commit(value: str, *, path: str) -> str:
    raw = str(value).strip().casefold()
    if (
        len(raw) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in raw)
    ):
        raise ValueError(f"{path} must be a full immutable Git commit")
    return raw


def _normalize_task_key(value: str) -> str:
    normalized = " ".join(str(value).strip().split()).casefold()
    if not normalized:
        raise ValueError("canonical task key must be non-empty")
    return normalized


def _repository_ids_match(left: str, right: str) -> bool:
    return _repository_slug(left) == _repository_slug(right)


def _repository_slug(value: str) -> str:
    raw = str(value).strip()
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.scheme != "file":
        path = unquote(parsed.path)
    elif parsed.scheme == "file":
        path = unquote(parsed.path)
    else:
        scp_parts = raw.split(":", 1)
        if (
            len(scp_parts) == 2
            and "/" in scp_parts[1]
            and "/" not in scp_parts[0]
        ):
            path = scp_parts[1]
        else:
            path = raw
    normalized = path.replace("\\", "/").strip("/").casefold()
    for prefix in ("github.com/", "gitlab.com/", "bitbucket.org/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


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


def run_task_spec_bound_official_harness_oracle(
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed until an execution backend attests immutable authority.

    The stock ``swebench.harness.run_evaluation`` CLI internally derives its
    package, image, network, and container settings.  Binding requested values
    into a command or receipt therefore does not prove they were enforced.
    This production-facing adapter validates all immutable inputs, then returns
    an unavailable result carrying an explicit incomplete authority record.
    A grade becomes available only when a trusted backend returns a complete
    authority accepted by :class:`SweBenchVerifier`.
    """
    execution_spec = _validated_bound_execution_spec(context)
    instance_id = execution_spec.instance_id
    candidate_id = _canonical_sha256(
        _required_text(context, "candidate_id"),
        path="official_oracle_context.candidate_id",
    )
    frozen_result_hash = _canonical_sha256(
        _required_text(context, "frozen_result_hash"),
        path="official_oracle_context.frozen_result_hash",
    )
    if candidate_id != frozen_result_hash:
        raise ValueError(
            "official SWE-bench oracle candidate_id does not match frozen result"
        )
    model_patch = str(context.get("model_patch") or "")
    if not model_patch.strip():
        return _bound_adapter_failure(
            execution_spec=execution_spec,
            context=context,
            command=[],
            return_code=2,
            stdout="",
            stderr="missing model_patch for official SWE-bench oracle",
            artifact_paths={},
            reason="missing_model_patch",
        )
    model_patch_sha256 = sha256(model_patch.encode("utf-8")).hexdigest()
    if _required_text(context, "model_patch_sha256").casefold() != (
        model_patch_sha256
    ):
        raise ValueError(
            "official SWE-bench oracle model_patch_sha256 does not match patch"
        )
    producer_run_result_hash = _canonical_sha256(
        _required_text(context, "producer_run_result_hash"),
        path="official_oracle_context.producer_run_result_hash",
    )
    request_nonce = _canonical_sha256(
        _required_text(context, "request_nonce"),
        path="official_oracle_context.request_nonce",
    )

    harness = execution_spec.harness
    run_id = _safe_filename(
        str(harness["run_id_prefix"]),
        instance_id,
        candidate_id,
        request_nonce[:16],
        max_length=180,
    )
    backend_id = SWE_BENCH_STOCK_UNATTESTED_BACKEND_ID
    backend_manifest = {
        "schema_version": "supervisor-swe-bench-backend-manifest/v1",
        "backend_id": backend_id,
        "entrypoint": "swebench.harness.run_evaluation",
        "authority_mode": "unattested",
        "gradeable": False,
    }
    backend_manifest_hash = sha256(
        _canonical_json_bytes(backend_manifest)
    ).hexdigest()
    unavailable_outcome = {
        "return_code": 2,
        "oracle_unavailable": True,
        "patch_applied": False,
        "report_sha256": None,
        "report_instance_id": "",
        "resolved": False,
        "fail_to_pass_status": "unavailable",
        "pass_to_pass_status": "unavailable",
        "fail_to_pass_results_hash": None,
        "pass_to_pass_results_hash": None,
    }
    execution_authority = build_swe_bench_execution_authority(
        execution_spec=execution_spec,
        mode="unattested",
        backend_id=backend_id,
        backend_manifest_hash=backend_manifest_hash,
        candidate_id=candidate_id,
        model_patch_sha256=model_patch_sha256,
        producer_run_result_hash=producer_run_result_hash,
        request_nonce=request_nonce,
        backend_run_id=run_id,
        observed_pins=None,
        pin_evidence={
            pin: {
                "kind": "unattested-enforcement-gap",
                "ref": f"{backend_id}#{pin}",
                "sha256": _sha256_json({
                    "backend_manifest_hash": backend_manifest_hash,
                    "pin": pin,
                    "reason": (
                        "stock SWE-bench Python CLI does not emit backend "
                        "enforcement evidence for this pin"
                    ),
                }),
            }
            for pin in SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS
        },
        outcome=unavailable_outcome,
    )
    return _bound_adapter_failure(
        execution_spec=execution_spec,
        context=context,
        command=[],
        return_code=2,
        stdout="",
        stderr=(
            "the stock SWE-bench Python CLI derives container and execution "
            "settings internally and cannot attest every TaskSpec pin"
        ),
        artifact_paths={},
        reason="execution_backend_attestation_required",
        execution_authority=execution_authority,
    )


def run_legacy_environment_selected_official_harness_oracle(
    context: Mapping[str, Any],
) -> dict[str, Any]:
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

    try:
        payload = json.loads(
            instance_report_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return _adapter_failure(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=f"{completed.stderr}\n{exc}",
            artifact_paths=artifact_paths,
            reason="official_instance_report_malformed",
        )
    if (
        not isinstance(payload, Mapping)
        or instance_id not in payload
        or not isinstance(payload[instance_id], Mapping)
    ):
        return _adapter_failure(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact_paths=artifact_paths,
            reason="official_instance_report_row_mismatch",
        )
    row = payload[instance_id]
    tests_status = row.get("tests_status")
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
    if not unavailable_reasons:
        overlap_reason = _cross_status_bucket_overlap_reason(tests_status)
        if overlap_reason:
            unavailable_reasons.append(overlap_reason)
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
    resolved = row.get("resolved")
    if not isinstance(resolved, bool):
        return _adapter_failure(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact_paths=artifact_paths,
            reason="official_report_resolved_missing_or_malformed",
        )
    expected_resolved = (
        fail_to_pass_status == "pass"
        and pass_to_pass_status == "pass"
    )
    if resolved is not expected_resolved:
        return _adapter_failure(
            context=context,
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifact_paths=artifact_paths,
            reason="official_report_resolved_status_mismatch",
        )

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


def run_official_harness_oracle(context: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the legacy environment-selected replay path.

    New TaskSpec-backed verification must call
    :func:`run_task_spec_bound_official_harness_oracle`.
    """
    return run_legacy_environment_selected_official_harness_oracle(context)


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

    if not fail_to_pass:
        return _pro_adapter_failure(
            context=context,
            command=run_command,
            return_code=run_result.returncode,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            artifact_paths=artifact_paths,
            reason="pro_oracle_bucket_empty:fail_to_pass",
            attempt_stage="scoring",
            docker_image=docker_image,
            docker_platform=docker_platform,
            pull_command=pull_command,
            pull_return_code=pull_result.returncode,
            patch_applied=True,
            test_command_return_code=test_command_return_code,
            source_script_evidence=source_script_evidence,
        )

    pass_to_pass_empty_vacuous_pass = not pass_to_pass
    fail_to_pass_status = "pass" if set(fail_to_pass) <= passed_tests else "fail"
    pass_to_pass_status = "pass" if set(pass_to_pass) <= passed_tests else "fail"
    rc_nonzero_resolved = (
        test_command_return_code not in (None, 0)
        and fail_to_pass_status == "pass"
        and pass_to_pass_status == "pass"
    )
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
        fail_to_pass_count=len(fail_to_pass),
        pass_to_pass_count=len(pass_to_pass),
        pass_to_pass_empty_vacuous_pass=pass_to_pass_empty_vacuous_pass,
        rc_nonzero_resolved=rc_nonzero_resolved,
    )
    return {
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "patch_applied": True,
        "fail_to_pass_count": len(fail_to_pass),
        "pass_to_pass_count": len(pass_to_pass),
        "pass_to_pass_empty_vacuous_pass": pass_to_pass_empty_vacuous_pass,
        "rc_nonzero_resolved": rc_nonzero_resolved,
        "oracle_adapter_receipt": receipt,
    }


def _validated_bound_execution_spec(
    context: Mapping[str, Any],
) -> SweBenchVerifierExecutionSpec:
    raw_spec = context.get("verifier_execution_spec")
    if not isinstance(raw_spec, Mapping):
        raise ValueError(
            "official SWE-bench oracle context missing verifier_execution_spec"
        )
    execution_spec = SweBenchVerifierExecutionSpec.from_mapping(raw_spec)
    observed_spec_hash = _required_text(
        context,
        "verifier_execution_spec_hash",
    ).casefold()
    if observed_spec_hash != execution_spec.execution_spec_hash:
        raise ValueError(
            "official SWE-bench oracle verifier_execution_spec_hash mismatch"
        )
    expected_context = execution_spec.context_binding()
    for key, expected in expected_context.items():
        if key in {
            "verifier_execution_spec",
            "verifier_execution_spec_hash",
        }:
            continue
        if key not in context:
            raise ValueError(
                f"official SWE-bench oracle context missing bound field {key}"
            )
        observed = _plain_json(
            context[key],
            path=f"official_oracle_context.{key}",
        )
        if observed != expected:
            raise ValueError(
                "official SWE-bench oracle context field "
                f"{key} does not match verifier execution spec"
            )
    _validate_bound_context_aliases(context, execution_spec)
    return execution_spec


def _validate_bound_context_aliases(
    context: Mapping[str, Any],
    execution_spec: SweBenchVerifierExecutionSpec,
) -> None:
    aliases: dict[str, Any] = {
        "dataset": execution_spec.dataset_name,
        "dataset_id": execution_spec.dataset_name,
        "dataset_sha256": execution_spec.dataset_hash,
        "dataset_split": execution_spec.split,
        "split_name": execution_spec.split,
        "task_instance_id": execution_spec.instance_id,
        "repository": execution_spec.repository,
        "commit": execution_spec.revision,
        "commit_sha": execution_spec.revision,
        "docker_image": execution_spec.container_image,
        "container_digest": execution_spec.image_digest,
        "platform_architecture": execution_spec.architecture,
        "platform_os": execution_spec.os_name,
        "network": execution_spec.network_policy,
        "limits": _thaw_json(execution_spec.resource_limits),
    }
    for key, expected in aliases.items():
        if key not in context:
            continue
        observed = _plain_json(
            context[key],
            path=f"official_oracle_context.{key}",
        )
        if observed != expected:
            raise ValueError(
                "official SWE-bench oracle context alias "
                f"{key} does not match verifier execution spec"
            )


def _bound_adapter_failure(
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
    reason: str,
    execution_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    authority_run_id = (
        str(execution_authority.get("backend_run_id") or "")
        if isinstance(execution_authority, Mapping)
        else ""
    )
    receipt = _bound_adapter_receipt(
        execution_spec=execution_spec,
        context=context,
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status="unavailable",
        pass_to_pass_status="unavailable",
        run_id=authority_run_id,
        oracle_unavailable=True,
        unavailable_reason=reason,
        execution_authority=execution_authority,
    )
    result = {
        "fail_to_pass_status": "unavailable",
        "pass_to_pass_status": "unavailable",
        "oracle_unavailable": True,
        "oracle_unavailable_reason": reason,
        "verifier_execution_spec_hash": execution_spec.execution_spec_hash,
        "oracle_adapter_receipt": receipt,
    }
    if execution_authority is not None:
        authority = _plain_json_mapping(
            execution_authority,
            path="execution_authority",
        )
        result.update(
            _canonical_execution_authority_outcome(
                authority.get("outcome"),
                execution_spec=execution_spec,
            )
        )
        result["execution_authority"] = authority
        result["execution_authority_hash"] = str(
            authority.get("authority_hash") or ""
        )
    return result


def _bound_adapter_receipt(
    *,
    execution_spec: SweBenchVerifierExecutionSpec,
    context: Mapping[str, Any],
    command: list[str],
    return_code: int,
    stdout: str,
    stderr: str,
    artifact_paths: Mapping[str, str],
    fail_to_pass_status: str,
    pass_to_pass_status: str,
    run_id: str,
    oracle_unavailable: bool = False,
    unavailable_reason: str = "",
    execution_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = _adapter_receipt(
        context=context,
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        artifact_paths=artifact_paths,
        fail_to_pass_status=fail_to_pass_status,
        pass_to_pass_status=pass_to_pass_status,
        dataset=execution_spec.dataset_name,
        split=execution_spec.split,
        run_id=run_id,
        oracle_unavailable=oracle_unavailable,
        unavailable_reason=unavailable_reason,
    )
    receipt.update({
        "schema_version": SWE_BENCH_BOUND_ORACLE_RECEIPT_SCHEMA_VERSION,
        "verifier_execution_spec": execution_spec.to_dict(),
        "verifier_execution_spec_hash": (
            execution_spec.execution_spec_hash
        ),
        "task_spec_hash": execution_spec.task_spec_hash,
        "canonical_task_id": execution_spec.canonical_task_id,
        "instance_row_hash": execution_spec.instance_row_hash,
        "verifier_package": execution_spec.verifier_package,
        "verifier_hash": execution_spec.verifier_hash,
        "container": {
            "image": execution_spec.container_image,
            "digest": execution_spec.image_digest,
        },
        "platform": {
            "architecture": execution_spec.architecture,
            "os_name": execution_spec.os_name,
        },
        "network_policy": execution_spec.network_policy,
        "resource_limits": _thaw_json(execution_spec.resource_limits),
        "bound_harness_configuration": _thaw_json(
            execution_spec.harness
        ),
        "candidate_id": str(context.get("candidate_id") or ""),
        "producer_run_result_hash": str(
            context.get("producer_run_result_hash") or ""
        ),
        "request_nonce": str(context.get("request_nonce") or ""),
    })
    if execution_authority is not None:
        authority = _plain_json_mapping(
            execution_authority,
            path="execution_authority",
        )
        receipt.update(
            _canonical_execution_authority_outcome(
                authority.get("outcome"),
                execution_spec=execution_spec,
            )
        )
        receipt["execution_authority"] = authority
        receipt["execution_authority_hash"] = str(
            authority.get("authority_hash") or ""
        )
    return receipt


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
    fail_to_pass_count: int | None = None,
    pass_to_pass_count: int | None = None,
    pass_to_pass_empty_vacuous_pass: bool = False,
    rc_nonzero_resolved: bool = False,
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
    if fail_to_pass_count is not None:
        receipt["fail_to_pass_count"] = int(fail_to_pass_count)
    if pass_to_pass_count is not None:
        receipt["pass_to_pass_count"] = int(pass_to_pass_count)
        receipt["pass_to_pass_empty_vacuous_pass"] = bool(
            pass_to_pass_empty_vacuous_pass
        )
    if rc_nonzero_resolved:
        receipt["rc_nonzero_resolved"] = True
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
    if "success" not in bucket:
        return "unavailable", f"{key}_success_missing"
    if "failure" not in bucket:
        return "unavailable", f"{key}_failure_missing"
    successes = bucket["success"]
    failures = bucket["failure"]
    if not isinstance(successes, Sequence) or isinstance(
        successes,
        (str, bytes),
    ):
        return "unavailable", f"{key}_success_malformed"
    if not isinstance(failures, Sequence) or isinstance(failures, (str, bytes)):
        return "unavailable", f"{key}_failure_malformed"
    if any(
        not isinstance(test_id, str) or not test_id.strip()
        for test_id in successes
    ):
        return "unavailable", f"{key}_success_entry_malformed"
    if any(
        not isinstance(test_id, str) or not test_id.strip()
        for test_id in failures
    ):
        return "unavailable", f"{key}_failure_entry_malformed"
    normalized_successes = [test_id.strip() for test_id in successes]
    normalized_failures = [test_id.strip() for test_id in failures]
    if len(set(normalized_successes)) != len(normalized_successes):
        return "unavailable", f"{key}_success_duplicate"
    if len(set(normalized_failures)) != len(normalized_failures):
        return "unavailable", f"{key}_failure_duplicate"
    if set(normalized_successes) & set(normalized_failures):
        return "unavailable", f"{key}_success_failure_overlap"
    if not successes and not failures:
        return "unavailable", f"{key}_bucket_empty"
    return ("fail" if failures else "pass"), ""


def _cross_status_bucket_overlap_reason(tests_status: Any) -> str:
    if not isinstance(tests_status, Mapping):
        return ""
    bucket_ids: dict[str, set[str]] = {}
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        bucket = tests_status.get(key)
        if not isinstance(bucket, Mapping):
            return ""
        identifiers: set[str] = set()
        for outcome in ("success", "failure"):
            values = bucket.get(outcome)
            if not isinstance(values, Sequence) or isinstance(
                values,
                (str, bytes),
            ):
                return ""
            identifiers.update(str(value).strip() for value in values)
        bucket_ids[key] = identifiers
    if bucket_ids["FAIL_TO_PASS"] & bucket_ids["PASS_TO_PASS"]:
        return "FAIL_TO_PASS_PASS_TO_PASS_test_id_overlap"
    return ""


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

    Mirrors upstream ``create_entryscript`` (scaleapi/SWE-bench_Pro-os,
    pinned commit ``ca10a60a``): the dataset's ``before_repo_set_cmd`` is
    collapsed to its last non-empty line and dropped verbatim after
    ``git apply``. Only the upstream patch-wipe foot-gun is carved out --
    if that last line is a ``git reset|clean|checkout`` invocation, it is
    hoisted above ``git apply`` so the just-applied patch is not wiped.
    The run_script is wrapped so its exit code is captured to
    ``test_command.json`` and the parser still runs on the captured logs.
    Without the wrapper, ``set -e`` would abort the script before
    ``parser.py`` writes ``output.json``, causing legitimate test failures
    to be misreported as ``pro_parser_output_missing`` instead of yielding
    a ``fail`` status from the parsed test results.
    """
    selected = ",".join(str(item) for item in selected_tests)
    selected_arg = shlex.quote(selected)
    pre_patch_extra, post_patch_line = _classify_pro_before_repo_set_cmd(
        before_repo_set_cmd
    )

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "cd /app",
        f"git reset --hard {shlex.quote(base_commit)}",
        f"git checkout {shlex.quote(base_commit)}",
    ]
    if pre_patch_extra:
        lines.append(pre_patch_extra)
    lines.extend([
        "if git apply -v /workspace/patch.diff; then",
        "  printf '{\"patch_applied\": true}\\n' > /workspace/patch_apply.json",
        "else",
        "  status=$?",
        "  printf '{\"patch_applied\": false, \"return_code\": %s}\\n' \"$status\" > /workspace/patch_apply.json",
        "  exit \"$status\"",
        "fi",
    ])
    if post_patch_line:
        lines.append(post_patch_line)
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


def _classify_pro_before_repo_set_cmd(value: str) -> tuple[str, str]:
    """Return ``(pre_patch_extra, post_patch_line)`` for an entryscript.

    Upstream uses ``sample["before_repo_set_cmd"].strip().split("\\n")[-1]``
    verbatim after ``git apply``. The single carve-out is the patch-wipe
    foot-gun: when that last line is a ``git reset|clean|checkout`` that
    would erase the applied patch, hoist it ahead of ``git apply`` instead.
    """
    last_line = _last_nonempty_line(value)
    if not last_line:
        return "", ""
    if _is_pro_pre_patch_repo_setup(last_line):
        return last_line, ""
    return "", last_line


def _last_nonempty_line(value: str) -> str:
    for raw_line in reversed(str(value).splitlines()):
        line = raw_line.strip()
        if line:
            return line
    return ""


_PRO_SHELL_OPERATORS: tuple[str, ...] = ("&&", "||", ";", "|")


def _is_pro_pre_patch_repo_setup(command: str) -> bool:
    if any(operator in command for operator in _PRO_SHELL_OPERATORS):
        return False
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
    repo_slug = repo.lower()
    repo_base, repo_name = repo_slug.split("/", 1)
    hsh = instance_id.replace("instance_", "").lower()
    hsh = hsh.removeprefix(f"{repo_base}__{repo_name}-")
    if repo_slug == "element-hq/element-web" and instance_id.endswith("-vnan"):
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
