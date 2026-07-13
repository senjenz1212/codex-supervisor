"""Fail-closed preregistration and readiness gate for Harness v1 pilots.

The pilot is an external, potentially expensive operation.  This module does
not run it.  It freezes the protocol and verifies that every launch
prerequisite is represented by a hash-pinned, parseable receipt.  Descriptive
or self-asserted booleans are insufficient.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

from .efficacy_analysis import (
    MAX_PREREGISTERED_B_WIN_RATE,
    MIN_CONFIRMATION_POWER,
    MIN_PREREGISTERED_B_WIN_RATE,
)
from .task_environment import canonical_task_identity


PILOT_PROTOCOL_SCHEMA_VERSION = "supervisor-pilot-protocol/v1"
PILOT_READINESS_SCHEMA_VERSION = "supervisor-pilot-readiness/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_FORBIDDEN_PROTOCOL_KEYS = frozenset({
    "observed_discordance",
    "discordance_rate",
    "observed_effect",
    "pilot_outcomes",
    "task_results",
    "winning_arm",
})
_PROTOCOL_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


EvidenceResolver = Callable[[str], bytes | bytearray | memoryview | None]


class ReceiptSignatureVerifier(Protocol):
    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        ...


class PilotReadinessError(ValueError):
    """A pilot protocol or readiness receipt is invalid."""


def _freeze_json(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise PilotReadinessError(
                    f"{path} keys must be non-empty strings"
                )
            frozen[raw_key] = _freeze_json(
                child,
                path=f"{path}.{raw_key}",
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PilotReadinessError(
                f"{path} must not contain non-finite numbers"
            )
        return value
    raise PilotReadinessError(f"{path} must contain only canonical JSON")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(child)
            for key, child in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True)
class ReceiptAttestation:
    authority_id: str
    signature: Mapping[str, Any]

    def __post_init__(self) -> None:
        authority_id = str(self.authority_id).strip()
        if not authority_id:
            raise PilotReadinessError(
                "receipt attestation authority_id must be non-empty"
            )
        if not isinstance(self.signature, Mapping) or not self.signature:
            raise PilotReadinessError(
                "receipt attestation signature must be non-empty"
            )
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(
            self,
            "signature",
            _freeze_json(self.signature, path="receipt attestation signature"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "signed_origin",
            "authority_id": self.authority_id,
            "signature": _thaw_json(self.signature),
        }


@dataclass(frozen=True)
class EvidencePin:
    """Content identity for one launch prerequisite receipt."""

    ref: str
    sha256: str
    attestation: ReceiptAttestation | None = None

    def __post_init__(self) -> None:
        ref = str(self.ref).strip()
        digest = str(self.sha256).strip().lower()
        if not ref:
            raise PilotReadinessError("evidence ref must be non-empty")
        if not _SHA256_RE.fullmatch(digest):
            raise PilotReadinessError("evidence sha256 must be canonical")
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "sha256": self.sha256,
            "attestation": (
                None
                if self.attestation is None
                else self.attestation.to_dict()
            ),
        }


@dataclass(frozen=True, init=False)
class FrozenPilotProtocol:
    """Canonical, outcome-free pilot preregistration."""

    payload: Mapping[str, Any]
    protocol_hash: str
    task_set_hash: str

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise PilotReadinessError(
            "FrozenPilotProtocol must be created by freeze_pilot_protocol"
        )

    @classmethod
    def _create(
        cls,
        *,
        payload: Mapping[str, Any],
        protocol_hash: str,
        task_set_hash: str,
    ) -> "FrozenPilotProtocol":
        instance = object.__new__(cls)
        object.__setattr__(instance, "payload", payload)
        object.__setattr__(instance, "protocol_hash", protocol_hash)
        object.__setattr__(instance, "task_set_hash", task_set_hash)
        return instance

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PILOT_PROTOCOL_SCHEMA_VERSION,
            "protocol_hash": self.protocol_hash,
            "task_set_hash": self.task_set_hash,
            "protocol": _thaw_json(self.payload),
        }


@dataclass(frozen=True)
class ReadinessFinding:
    code: str
    detail: str
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, init=False)
class PilotReadinessReport:
    protocol_hash: str
    task_set_hash: str
    findings: tuple[ReadinessFinding, ...]
    verified_receipts: tuple[EvidencePin, ...]
    authorization_valid_from_ms: int
    authorization_valid_until_ms: int
    report_hash: str
    schema_version: str = PILOT_READINESS_SCHEMA_VERSION

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise PilotReadinessError(
            "PilotReadinessReport must be created by "
            "validate_pilot_readiness"
        )

    @classmethod
    def _create(
        cls,
        *,
        protocol_hash: str,
        task_set_hash: str,
        findings: tuple[ReadinessFinding, ...],
        verified_receipts: tuple[EvidencePin, ...],
        authorization_valid_from_ms: int,
        authorization_valid_until_ms: int,
        report_hash: str,
    ) -> "PilotReadinessReport":
        instance = object.__new__(cls)
        object.__setattr__(instance, "protocol_hash", protocol_hash)
        object.__setattr__(instance, "task_set_hash", task_set_hash)
        object.__setattr__(instance, "findings", findings)
        object.__setattr__(
            instance,
            "verified_receipts",
            verified_receipts,
        )
        object.__setattr__(
            instance,
            "authorization_valid_from_ms",
            authorization_valid_from_ms,
        )
        object.__setattr__(
            instance,
            "authorization_valid_until_ms",
            authorization_valid_until_ms,
        )
        object.__setattr__(
            instance,
            "report_hash",
            report_hash,
        )
        object.__setattr__(
            instance,
            "schema_version",
            PILOT_READINESS_SCHEMA_VERSION,
        )
        return instance

    @property
    def ready(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": "ready" if self.ready else "blocked",
            "protocol_hash": self.protocol_hash,
            "task_set_hash": self.task_set_hash,
            "findings": [finding.to_dict() for finding in self.findings],
            "verified_receipts": [
                receipt.to_dict() for receipt in self.verified_receipts
            ],
            "authorization_valid_from_ms": (
                self.authorization_valid_from_ms
            ),
            "authorization_valid_until_ms": (
                self.authorization_valid_until_ms
            ),
            "report_hash": self.report_hash,
        }


def freeze_pilot_protocol(payload: Mapping[str, Any]) -> FrozenPilotProtocol:
    """Validate and hash an immutable, outcome-free pilot protocol."""
    if not isinstance(payload, Mapping):
        raise PilotReadinessError("pilot protocol must be a mapping")
    normalized = _normalise_json(payload)
    if normalized.get("schema_version") != PILOT_PROTOCOL_SCHEMA_VERSION:
        raise PilotReadinessError(
            f"pilot protocol schema must be {PILOT_PROTOCOL_SCHEMA_VERSION}"
        )
    forbidden = sorted(
        path
        for path, _value in _walk_mapping(normalized)
        if path.rsplit(".", 1)[-1] in _FORBIDDEN_PROTOCOL_KEYS
    )
    if forbidden:
        raise PilotReadinessError(
            "pilot protocol contains outcome-dependent fields: "
            + ", ".join(forbidden)
        )

    _require_text(normalized, "experiment_id")
    _require_text(normalized, "run_id")
    _require_text(normalized, "client_token")
    commit_sha = _require_text(normalized, "commit_sha").lower()
    if not _GIT_SHA_RE.fullmatch(commit_sha):
        raise PilotReadinessError("commit_sha must be a full Git object id")

    task_ids = _require_unique_text_sequence(normalized, "task_ids")
    pilot_canonical_task_ids = _require_task_identity_mapping(
        normalized,
        "task_identities",
        task_ids,
    )
    task_families = _require_task_family_mapping(
        normalized,
        task_ids=task_ids,
    )
    runtimes = _require_protocol_identifiers(normalized, "runtimes")
    declared_task_count = normalized.get("task_count")
    if (
        isinstance(declared_task_count, bool)
        or not isinstance(declared_task_count, int)
        or declared_task_count != len(task_ids)
        or declared_task_count <= 0
    ):
        raise PilotReadinessError(
            "task_count must equal the frozen unique task roster length"
        )
    excluded: set[str] = set()
    excluded_canonical: set[str] = set()
    for field, identity_field in (
        ("confirmation_task_ids", "confirmation_task_identities"),
        ("sealed_holdout_task_ids", "sealed_holdout_task_identities"),
        ("portability_task_ids", "portability_task_identities"),
    ):
        values = _require_unique_text_sequence(normalized, field)
        overlap = sorted(set(task_ids) & set(values))
        if overlap:
            raise PilotReadinessError(
                f"pilot task overlap with {field}: " + ", ".join(overlap[:10])
            )
        duplicate_exclusions = sorted(excluded & set(values))
        if duplicate_exclusions:
            raise PilotReadinessError(
                "reserved task sets overlap: "
                + ", ".join(duplicate_exclusions[:10])
            )
        excluded.update(values)
        canonical_values = _require_task_identity_mapping(
            normalized,
            identity_field,
            values,
        )
        canonical_overlap = sorted(
            set(pilot_canonical_task_ids) & set(canonical_values)
        )
        if canonical_overlap:
            raise PilotReadinessError(
                f"pilot canonical task overlap with {field}: "
                + ", ".join(canonical_overlap[:10])
            )
        duplicate_canonical_exclusions = sorted(
            excluded_canonical & set(canonical_values)
        )
        if duplicate_canonical_exclusions:
            raise PilotReadinessError(
                "reserved canonical task sets overlap: "
                + ", ".join(duplicate_canonical_exclusions[:10])
            )
        excluded_canonical.update(canonical_values)

    budgets = normalized.get("arm_budgets")
    if not isinstance(budgets, Mapping) or set(budgets) != {"A", "B", "C"}:
        raise PilotReadinessError("arm_budgets must define exactly A, B, and C")
    normalized_budgets = {
        arm: _validated_budget(arm, value)
        for arm, value in budgets.items()
    }
    if normalized_budgets["B"] != normalized_budgets["C"]:
        raise PilotReadinessError(
            "B and C must have identical ex-ante compute ceilings"
        )

    assignment = normalized.get("assignment")
    if not isinstance(assignment, Mapping):
        raise PilotReadinessError("assignment must be a mapping")
    _require_text(assignment, "version")
    _require_text(assignment, "sticky_key_algorithm")
    _require_text(assignment, "key_custodian")
    if assignment.get("persist_before_execution") is not True:
        raise PilotReadinessError(
            "assignment must be persisted before execution"
        )

    stop_rule = normalized.get("stop_rule")
    if not isinstance(stop_rule, Mapping):
        raise PilotReadinessError("stop_rule must be a mapping")
    if stop_rule.get("outcome_dependent") is not False:
        raise PilotReadinessError("pilot stop rule must not depend on outcomes")
    if stop_rule.get("exact_task_count") != len(task_ids):
        raise PilotReadinessError(
            "stop_rule.exact_task_count must equal the frozen roster length"
        )
    if stop_rule.get("run_until_discordant_pairs") not in (None, False, 0):
        raise PilotReadinessError(
            "pilot may not run until a desired discordant-pair count"
        )

    retry_policy = normalized.get("retry_policy")
    if not isinstance(retry_policy, Mapping):
        raise PilotReadinessError("retry_policy must be a mapping")
    if retry_policy.get("treatment_failure_is_itt_failure") is not True:
        raise PilotReadinessError(
            "treatment failures must remain intention-to-treat failures"
        )
    if retry_policy.get("max_common_infra_block_reruns") != 1:
        raise PilotReadinessError(
            "exactly one whole-block common-infrastructure rerun is permitted"
        )
    if retry_policy.get("selective_arm_rerun") is not False:
        raise PilotReadinessError("selective arm reruns are forbidden")

    alternative = normalized.get("alternative_b_win_rate")
    if (
        isinstance(alternative, bool)
        or not isinstance(alternative, (int, float))
        or not math.isfinite(float(alternative))
        or not MIN_PREREGISTERED_B_WIN_RATE
        <= float(alternative)
        <= MAX_PREREGISTERED_B_WIN_RATE
    ):
        raise PilotReadinessError(
            "alternative_b_win_rate must stay within the preregistered "
            f"[{MIN_PREREGISTERED_B_WIN_RATE:.2f}, "
            f"{MAX_PREREGISTERED_B_WIN_RATE:.2f}] range"
        )
    target_power = normalized.get("target_power")
    if (
        isinstance(target_power, bool)
        or not isinstance(target_power, (int, float))
        or not math.isfinite(float(target_power))
        or not MIN_CONFIRMATION_POWER <= float(target_power) < 1.0
    ):
        raise PilotReadinessError(
            f"target_power must be at least {MIN_CONFIRMATION_POWER:.2f}"
        )
    if normalized.get("discordance_bound_method") != "wilson-lower-95":
        raise PilotReadinessError(
            "discordance_bound_method must be wilson-lower-95"
        )

    for field in (
        "runtime_pins",
        "model_pins",
        "prompt_pins",
        "tool_contract_pins",
        "cli_pins",
        "image_pins",
        "verifier_pins",
        "network_resource_policy",
    ):
        _require_nonempty_pinned_mapping(normalized, field)
    required_task_families = tuple(sorted(set(task_families.values())))
    for field, keys in (
        ("runtime_pins", runtimes),
        ("cli_pins", runtimes),
        ("verifier_pins", required_task_families),
        ("image_pins", required_task_families),
        ("model_pins", ("A", "B", "C")),
        ("prompt_pins", ("A", "B", "C")),
    ):
        pins = normalized.get(field)
        if not isinstance(pins, Mapping) or set(pins) != set(keys):
            raise PilotReadinessError(
                f"{field} keys must exactly match: "
                + ", ".join(sorted(keys))
            )
        for key in keys:
            _protocol_sha256_pin(normalized, field, key)

    canonical = _canonical_json(normalized)
    frozen_payload = _freeze_json(normalized, path="pilot protocol")
    return FrozenPilotProtocol._create(
        payload=frozen_payload,
        protocol_hash=_sha256_bytes(canonical),
        task_set_hash=_sha256_json(sorted(pilot_canonical_task_ids)),
    )


def validate_pilot_readiness(
    protocol: FrozenPilotProtocol,
    *,
    receipts: Mapping[str, EvidencePin],
    evidence_resolver: EvidenceResolver,
    trusted_receipt_verifiers: (
        Mapping[str, ReceiptSignatureVerifier | Callable[..., bool]]
        | None
    ) = None,
    now_ms: int | None = None,
) -> PilotReadinessReport:
    """Verify every launch receipt and return a deterministic blocked/ready report."""
    _validate_frozen_protocol_integrity(protocol)
    observed_now_ms = _validated_now_ms(now_ms)
    runtimes = tuple(protocol.payload["runtimes"])
    task_families = tuple(sorted(
        set(protocol.payload["task_families"].values())
    ))
    required_receipts = {
        "worktree",
        "component_gate",
        "operational_tracer",
        "budget_authorization",
        "reviewer_acceptance",
        *(f"runtime_{runtime}" for runtime in runtimes),
        *(f"verifier_{family}" for family in task_families),
    }
    findings: list[ReadinessFinding] = []
    verified: list[EvidencePin] = []
    payloads: dict[str, dict[str, Any]] = {}

    for name in sorted(required_receipts):
        pin = receipts.get(name)
        if pin is None:
            findings.append(ReadinessFinding(
                code=f"missing_{name}_receipt",
                detail=f"required readiness receipt is missing: {name}",
            ))
            continue
        try:
            payload = _resolve_json_receipt(
                name,
                pin,
                evidence_resolver,
                protocol_hash=protocol.protocol_hash,
                trusted_receipt_verifiers=(
                    trusted_receipt_verifiers or {}
                ),
            )
        except PilotReadinessError as exc:
            findings.append(ReadinessFinding(
                code=f"invalid_{name}_receipt",
                detail=str(exc),
                evidence_ref=pin.ref,
            ))
            continue
        payloads[name] = payload
        verified.append(pin)

    commit_sha = str(protocol.payload["commit_sha"])
    _validate_status_receipt(
        payloads,
        "component_gate",
        findings,
        expected_status="passed",
        expected_commit_sha=commit_sha,
    )
    _validate_worktree_receipt(
        payloads.get("worktree"),
        findings,
        expected_commit_sha=commit_sha,
    )
    _validate_operational_tracer(
        payloads.get("operational_tracer"),
        findings,
        expected_commit_sha=commit_sha,
        expected_task_families=frozenset(task_families),
        expected_runtimes=frozenset(runtimes),
    )
    for runtime in runtimes:
        _validate_runtime_receipt(
            payloads.get(f"runtime_{runtime}"),
            findings,
            expected_runtime=runtime,
            expected_commit_sha=commit_sha,
            expected_runtime_sha256=_protocol_sha256_pin(
                protocol.payload,
                "runtime_pins",
                runtime,
            ),
            expected_executable_sha256=_protocol_sha256_pin(
                protocol.payload,
                "cli_pins",
                runtime,
            ),
        )
    for family in task_families:
        _validate_verifier_receipt(
            payloads.get(f"verifier_{family}"),
            findings,
            expected_family=family,
            expected_commit_sha=commit_sha,
            expected_verifier_sha256=_protocol_sha256_pin(
                protocol.payload,
                "verifier_pins",
                family,
            ),
        )
    authorization_window = _validate_budget_receipt(
        payloads.get("budget_authorization"),
        findings,
        protocol_hash=protocol.protocol_hash,
        now_ms=observed_now_ms,
    )
    _validate_reviewer_receipt(
        payloads.get("reviewer_acceptance"),
        findings,
        protocol_hash=protocol.protocol_hash,
        expected_commit_sha=commit_sha,
    )

    ordered_findings = tuple(sorted(
        findings,
        key=lambda finding: (
            finding.code,
            finding.evidence_ref or "",
            finding.detail,
        ),
    ))
    ordered_verified = tuple(sorted(verified, key=lambda pin: (pin.ref, pin.sha256)))
    report_payload = {
        "schema_version": PILOT_READINESS_SCHEMA_VERSION,
        "protocol_hash": protocol.protocol_hash,
        "task_set_hash": protocol.task_set_hash,
        "status": "ready" if not ordered_findings else "blocked",
        "findings": [finding.to_dict() for finding in ordered_findings],
        "verified_receipts": [pin.to_dict() for pin in ordered_verified],
        "authorization_valid_from_ms": authorization_window[0],
        "authorization_valid_until_ms": authorization_window[1],
    }
    return PilotReadinessReport._create(
        protocol_hash=protocol.protocol_hash,
        task_set_hash=protocol.task_set_hash,
        findings=ordered_findings,
        verified_receipts=ordered_verified,
        authorization_valid_from_ms=authorization_window[0],
        authorization_valid_until_ms=authorization_window[1],
        report_hash=_sha256_json(report_payload),
    )


def validate_pilot_execution_authorization(
    protocol: FrozenPilotProtocol,
    report: PilotReadinessReport,
    *,
    experiment_id: str,
    task: Mapping[str, Any] | Any,
    now_ms: int | None = None,
) -> None:
    """Fail closed unless a current ready report authorizes this exact task."""
    _validate_frozen_protocol_integrity(protocol)
    _validate_readiness_report_integrity(report)
    if not report.ready:
        raise PilotReadinessError(
            "pilot readiness report is blocked"
        )
    if (
        report.protocol_hash != protocol.protocol_hash
        or report.task_set_hash != protocol.task_set_hash
    ):
        raise PilotReadinessError(
            "pilot readiness report does not bind the frozen protocol"
        )
    observed_now_ms = _validated_now_ms(now_ms)
    if not (
        report.authorization_valid_from_ms
        <= observed_now_ms
        < report.authorization_valid_until_ms
    ):
        raise PilotReadinessError(
            "pilot launch authorization is stale"
        )
    expected_experiment_id = str(
        protocol.payload.get("experiment_id") or ""
    )
    if str(experiment_id) != expected_experiment_id:
        raise PilotReadinessError(
            "pilot experiment does not match the frozen protocol"
        )
    task_id = str(
        task.get("task_id")
        if isinstance(task, Mapping)
        else getattr(task, "task_id", "")
    )
    task_ids = tuple(protocol.payload["task_ids"])
    if task_id not in task_ids:
        raise PilotReadinessError(
            "pilot task is outside the frozen roster"
        )
    expected_identity = canonical_task_identity(
        protocol.payload["task_identities"][task_id]
    )
    try:
        observed_identity = canonical_task_identity(task)
    except ValueError as exc:
        raise PilotReadinessError(
            f"pilot execution task identity is invalid: {exc}"
        ) from exc
    if observed_identity != expected_identity:
        raise PilotReadinessError(
            "pilot execution task identity does not match the frozen roster"
        )
    task_family = str(
        task.get("task_family")
        if isinstance(task, Mapping)
        else getattr(task, "task_family", "")
    ).strip()
    if task_family != protocol.payload["task_families"][task_id]:
        raise PilotReadinessError(
            "pilot execution task family does not match the frozen protocol"
        )


def _validate_frozen_protocol_integrity(
    protocol: FrozenPilotProtocol,
) -> None:
    if not isinstance(protocol, FrozenPilotProtocol):
        raise PilotReadinessError(
            "pilot protocol must be a FrozenPilotProtocol"
        )
    rebuilt = freeze_pilot_protocol(_thaw_json(protocol.payload))
    if (
        protocol.protocol_hash != rebuilt.protocol_hash
        or protocol.task_set_hash != rebuilt.task_set_hash
        or _thaw_json(protocol.payload) != _thaw_json(rebuilt.payload)
    ):
        raise PilotReadinessError(
            "frozen pilot protocol integrity check failed"
        )


def _validate_readiness_report_integrity(
    report: PilotReadinessReport,
) -> None:
    if not isinstance(report, PilotReadinessReport):
        raise PilotReadinessError(
            "pilot readiness authorization is missing or invalid"
        )
    if report.schema_version != PILOT_READINESS_SCHEMA_VERSION:
        raise PilotReadinessError(
            "pilot readiness report schema is invalid"
        )
    report_payload = {
        "schema_version": report.schema_version,
        "protocol_hash": report.protocol_hash,
        "task_set_hash": report.task_set_hash,
        "status": "ready" if report.ready else "blocked",
        "findings": [finding.to_dict() for finding in report.findings],
        "verified_receipts": [
            receipt.to_dict() for receipt in report.verified_receipts
        ],
        "authorization_valid_from_ms": (
            report.authorization_valid_from_ms
        ),
        "authorization_valid_until_ms": (
            report.authorization_valid_until_ms
        ),
    }
    if not _SHA256_RE.fullmatch(report.report_hash) or (
        _sha256_json(report_payload) != report.report_hash
    ):
        raise PilotReadinessError(
            "pilot readiness report integrity check failed"
        )


def _resolve_json_receipt(
    receipt_name: str,
    pin: EvidencePin,
    resolver: EvidenceResolver,
    *,
    protocol_hash: str,
    trusted_receipt_verifiers: Mapping[
        str,
        ReceiptSignatureVerifier | Callable[..., bool],
    ],
) -> dict[str, Any]:
    attestation = pin.attestation
    if attestation is None:
        raise PilotReadinessError(
            f"receipt lacks authoritative signature: {pin.ref}"
        )
    verifier = trusted_receipt_verifiers.get(attestation.authority_id)
    if verifier is None:
        raise PilotReadinessError(
            f"receipt authority is not trusted: {attestation.authority_id}"
        )
    signed_payload = receipt_attestation_payload(
        receipt_name=receipt_name,
        evidence_ref=pin.ref,
        evidence_sha256=pin.sha256,
        protocol_hash=protocol_hash,
        authority_id=attestation.authority_id,
    )
    try:
        accepted = (
            verifier.verify(signed_payload, attestation.signature)
            if hasattr(verifier, "verify")
            else verifier(signed_payload, attestation.signature)
        )
    except Exception as exc:
        raise PilotReadinessError(
            f"receipt signature verification failed: {pin.ref}"
        ) from exc
    if accepted is not True:
        raise PilotReadinessError(
            f"receipt signature is invalid: {pin.ref}"
        )
    raw = resolver(pin.ref)
    if raw is None:
        raise PilotReadinessError(f"receipt cannot be resolved: {pin.ref}")
    data = bytes(raw)
    if _sha256_bytes(data) != pin.sha256:
        raise PilotReadinessError(f"receipt digest mismatch: {pin.ref}")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotReadinessError(
            f"receipt is not canonical JSON: {pin.ref}"
        ) from exc
    if not isinstance(payload, dict):
        raise PilotReadinessError(f"receipt must be a JSON object: {pin.ref}")
    return payload


def receipt_attestation_payload(
    *,
    receipt_name: str,
    evidence_ref: str,
    evidence_sha256: str,
    protocol_hash: str,
    authority_id: str,
) -> bytes:
    return _canonical_json({
        "schema_version": "supervisor-readiness-receipt-attestation/v1",
        "receipt_name": str(receipt_name),
        "evidence_ref": str(evidence_ref),
        "evidence_sha256": str(evidence_sha256).lower(),
        "protocol_hash": str(protocol_hash).lower(),
        "authority_id": str(authority_id),
    })


def _validate_status_receipt(
    payloads: Mapping[str, dict[str, Any]],
    name: str,
    findings: list[ReadinessFinding],
    *,
    expected_status: str,
    expected_commit_sha: str,
) -> None:
    payload = payloads.get(name)
    if payload is None:
        return
    if payload.get("status") != expected_status:
        findings.append(ReadinessFinding(
            code=f"{name}_not_{expected_status}",
            detail=f"{name} status must be {expected_status}",
        ))
    if payload.get("commit_sha") != expected_commit_sha:
        findings.append(ReadinessFinding(
            code=f"{name}_commit_mismatch",
            detail=f"{name} does not pin the protocol commit",
        ))


def _validate_worktree_receipt(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    expected_commit_sha: str,
) -> None:
    if payload is None:
        return
    if payload.get("clean") is not True or payload.get("status_porcelain") != "":
        findings.append(ReadinessFinding(
            code="execution_tree_not_clean",
            detail="pilot execution requires a clean commit-pinned worktree",
        ))
    if payload.get("head_commit") != expected_commit_sha:
        findings.append(ReadinessFinding(
            code="execution_tree_commit_mismatch",
            detail="worktree HEAD does not match the frozen protocol commit",
        ))


def _validate_operational_tracer(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    expected_commit_sha: str,
    expected_task_families: frozenset[str],
    expected_runtimes: frozenset[str],
) -> None:
    if payload is None:
        return
    requirements = {
        "status": "passed",
        "mode": "operational",
        "claim_level": "L2",
        "ledger_valid": True,
        "trace_closed": True,
        "hidden_verifier_isolated": True,
    }
    for field, expected in requirements.items():
        if payload.get(field) != expected:
            findings.append(ReadinessFinding(
                code=f"operational_tracer_{field}_invalid",
                detail=(
                    f"operational tracer {field} must be {expected!r}; "
                    f"observed {payload.get(field)!r}"
                ),
            ))
    if payload.get("commit_sha") != expected_commit_sha:
        findings.append(ReadinessFinding(
            code="operational_tracer_commit_mismatch",
            detail="operational tracer does not pin the protocol commit",
        ))
    if set(payload.get("task_families") or ()) != set(
        expected_task_families
    ):
        findings.append(ReadinessFinding(
            code="operational_tracer_task_families_incomplete",
            detail=(
                "operational tracer task families must exactly match the "
                "frozen protocol"
            ),
        ))
    if set(payload.get("runtimes") or ()) != set(expected_runtimes):
        findings.append(ReadinessFinding(
            code="operational_tracer_runtimes_incomplete",
            detail=(
                "operational tracer runtimes must exactly match the "
                "frozen protocol"
            ),
        ))
    if payload.get("external_provider_calls") is not True:
        findings.append(ReadinessFinding(
            code="operational_tracer_is_hermetic_only",
            detail="hermetic fake-runtime evidence cannot authorize a pilot",
        ))


def _validate_runtime_receipt(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    expected_runtime: str,
    expected_commit_sha: str,
    expected_runtime_sha256: str,
    expected_executable_sha256: str,
) -> None:
    if payload is None:
        return
    if (
        payload.get("status") != "available"
        or payload.get("runtime") != expected_runtime
        or payload.get("commit_sha") != expected_commit_sha
        or not _SHA256_RE.fullmatch(str(payload.get("executable_sha256") or ""))
        or not _SHA256_RE.fullmatch(
            str(payload.get("runtime_implementation_sha256") or "")
        )
        or not str(payload.get("version") or "").strip()
    ):
        findings.append(ReadinessFinding(
            code=f"runtime_{expected_runtime}_not_pinned_available",
            detail=(
                f"{expected_runtime} must be available with a version, "
                "executable hash, and matching commit"
            ),
        ))
        return
    if (
        payload.get("runtime_implementation_sha256")
        != expected_runtime_sha256
        or payload.get("executable_sha256")
        != expected_executable_sha256
    ):
        findings.append(ReadinessFinding(
            code=f"runtime_{expected_runtime}_protocol_pin_mismatch",
            detail=(
                f"{expected_runtime} receipt hashes do not match the "
                "frozen runtime and CLI pins"
            ),
        ))


def _validate_verifier_receipt(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    expected_family: str,
    expected_commit_sha: str,
    expected_verifier_sha256: str,
) -> None:
    if payload is None:
        return
    if (
        payload.get("status") != "available"
        or payload.get("task_family") != expected_family
        or payload.get("hidden") is not True
        or payload.get("independent") is not True
        or payload.get("commit_sha") != expected_commit_sha
        or not str(payload.get("verifier_id") or "").strip()
        or not str(payload.get("verifier_version") or "").strip()
        or not _SHA256_RE.fullmatch(
            str(payload.get("verifier_implementation_sha256") or "")
        )
    ):
        findings.append(ReadinessFinding(
            code=f"verifier_{expected_family}_not_pinned_available",
            detail=(
                f"{expected_family} verifier must be available, hidden, "
                "independent, versioned, and implementation-hashed"
            ),
        ))
        return
    if (
        payload.get("verifier_implementation_sha256")
        != expected_verifier_sha256
    ):
        findings.append(ReadinessFinding(
            code=f"verifier_{expected_family}_protocol_pin_mismatch",
            detail=(
                f"{expected_family} verifier receipt hash does not match "
                "the frozen verifier pin"
            ),
        ))


def _validate_budget_receipt(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    protocol_hash: str,
    now_ms: int,
) -> tuple[int, int]:
    if payload is None:
        return (0, 0)
    max_cost = payload.get("max_cost_usd")
    max_wall = payload.get("max_wall_time_s")
    valid_from_ms = payload.get("valid_from_ms")
    valid_until_ms = payload.get("valid_until_ms")
    if (
        payload.get("status") != "authorized"
        or payload.get("protocol_hash") != protocol_hash
        or not str(payload.get("authorized_by") or "").strip()
        or isinstance(max_cost, bool)
        or not isinstance(max_cost, (int, float))
        or not math.isfinite(float(max_cost))
        or float(max_cost) <= 0
        or isinstance(max_wall, bool)
        or not isinstance(max_wall, int)
        or max_wall <= 0
        or payload.get("credentials_authorized") is not True
        or payload.get("storage_authorized") is not True
        or isinstance(valid_from_ms, bool)
        or not isinstance(valid_from_ms, int)
        or valid_from_ms <= 0
        or isinstance(valid_until_ms, bool)
        or not isinstance(valid_until_ms, int)
        or valid_until_ms <= valid_from_ms
    ):
        findings.append(ReadinessFinding(
            code="budget_authorization_invalid",
            detail=(
                "named authorization must pin the protocol, credentials, "
                "storage, positive cost and wall-time ceilings, and a "
                "bounded validity window"
            ),
        ))
        return (0, 0)
    if not valid_from_ms <= now_ms < valid_until_ms:
        findings.append(ReadinessFinding(
            code="budget_authorization_stale",
            detail=(
                "budget authorization is not valid at readiness evaluation "
                "time"
            ),
        ))
    return (valid_from_ms, valid_until_ms)


def _validate_reviewer_receipt(
    payload: Mapping[str, Any] | None,
    findings: list[ReadinessFinding],
    *,
    protocol_hash: str,
    expected_commit_sha: str,
) -> None:
    if payload is None:
        return
    reviewers = payload.get("reviewers")
    accepted_reviewers = {
        str(item.get("reviewer") or "")
        for item in reviewers or ()
        if isinstance(item, Mapping) and item.get("decision") == "accept"
    }
    if (
        payload.get("status") != "accepted"
        or payload.get("protocol_hash") != protocol_hash
        or payload.get("commit_sha") != expected_commit_sha
        or not {"claude_code", "cursor_sdk"} <= accepted_reviewers
    ):
        findings.append(ReadinessFinding(
            code="reviewer_acceptance_invalid",
            detail=(
                "pilot launch requires commit- and protocol-pinned acceptance "
                "from both configured reviewer lanes"
            ),
        ))


def _validated_budget(arm: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotReadinessError(f"arm {arm} budget must be a mapping")
    required = ("max_attempts", "max_tokens", "max_cost_usd", "timeout_s")
    normalized: dict[str, Any] = {}
    for field in required:
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PilotReadinessError(
                f"arm {arm} budget {field} must be numeric"
            )
        numeric = float(raw)
        if not math.isfinite(numeric) or numeric <= 0:
            raise PilotReadinessError(
                f"arm {arm} budget {field} must be positive and finite"
            )
        normalized[field] = int(raw) if field != "max_cost_usd" else numeric
    return normalized


def _require_nonempty_pinned_mapping(
    payload: Mapping[str, Any],
    field: str,
) -> None:
    value = payload.get(field)
    if not isinstance(value, Mapping) or not value:
        raise PilotReadinessError(f"{field} must be a non-empty mapping")
    missing = [
        str(key)
        for key, item in value.items()
        if not _contains_sha256(item)
    ]
    if missing:
        raise PilotReadinessError(
            f"{field} contains unpinned entries: " + ", ".join(sorted(missing))
        )


def _protocol_sha256_pin(
    payload: Mapping[str, Any],
    field: str,
    key: str,
) -> str:
    pins = payload.get(field)
    value = pins.get(key) if isinstance(pins, Mapping) else None
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value
    if isinstance(value, Mapping):
        for candidate_key in ("sha256", "hash"):
            candidate = value.get(candidate_key)
            if (
                isinstance(candidate, str)
                and _SHA256_RE.fullmatch(candidate)
            ):
                return candidate
    raise PilotReadinessError(
        f"{field}.{key} must contain one canonical sha256 pin"
    )


def _contains_sha256(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_SHA256_RE.fullmatch(value))
    if isinstance(value, Mapping):
        return any(
            _contains_sha256(item)
            for key, item in value.items()
            if "sha256" in str(key) or "hash" in str(key)
        )
    return False


def _require_unique_text_sequence(
    payload: Mapping[str, Any],
    field: str,
) -> tuple[str, ...]:
    value = payload.get(field)
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise PilotReadinessError(f"{field} must be a sequence")
    normalized = tuple(str(item).strip() for item in value)
    if any(not item for item in normalized):
        raise PilotReadinessError(f"{field} entries must be non-empty")
    if len(set(normalized)) != len(normalized):
        raise PilotReadinessError(f"{field} must contain unique task IDs")
    return normalized


def _require_protocol_identifiers(
    payload: Mapping[str, Any],
    field: str,
) -> tuple[str, ...]:
    values = _require_unique_text_sequence(payload, field)
    invalid = [
        value
        for value in values
        if not _PROTOCOL_IDENTIFIER_RE.fullmatch(value)
    ]
    if invalid:
        raise PilotReadinessError(
            f"{field} entries must be lowercase protocol identifiers: "
            + ", ".join(invalid)
        )
    return values


def _require_task_family_mapping(
    payload: Mapping[str, Any],
    *,
    task_ids: Sequence[str],
) -> dict[str, str]:
    value = payload.get("task_families")
    if not isinstance(value, Mapping) or set(value) != set(task_ids):
        raise PilotReadinessError(
            "task_families keys must exactly match the pilot task roster"
        )
    normalized: dict[str, str] = {}
    for task_id in task_ids:
        family = str(value.get(task_id) or "").strip()
        if not _PROTOCOL_IDENTIFIER_RE.fullmatch(family):
            raise PilotReadinessError(
                f"task_families.{task_id} must be a lowercase "
                "protocol identifier"
            )
        normalized[task_id] = family
    return normalized


def _require_task_identity_mapping(
    payload: Mapping[str, Any],
    field: str,
    task_ids: Sequence[str],
) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise PilotReadinessError(f"{field} must be a mapping")
    if set(value) != set(task_ids):
        raise PilotReadinessError(
            f"{field} keys must exactly match its task roster"
        )
    canonical_ids: list[str] = []
    for task_id in task_ids:
        identity = value.get(task_id)
        if not isinstance(identity, Mapping):
            raise PilotReadinessError(
                f"{field}.{task_id} must be an identity mapping"
            )
        try:
            canonical_ids.append(canonical_task_identity(identity))
        except ValueError as exc:
            raise PilotReadinessError(
                f"{field}.{task_id} is invalid: {exc}"
            ) from exc
    if len(set(canonical_ids)) != len(canonical_ids):
        raise PilotReadinessError(
            f"{field} contains duplicate canonical task identities"
        )
    return tuple(canonical_ids)


def _require_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise PilotReadinessError(f"{field} must be non-empty")
    return value


def _validated_now_ms(value: int | None) -> int:
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PilotReadinessError("now_ms must be a positive integer")
    return value


def _walk_mapping(
    value: Any,
    prefix: str = "",
) -> Sequence[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.append((path, item))
            rows.extend(_walk_mapping(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            rows.extend(_walk_mapping(item, f"{prefix}[{index}]"))
    return rows


def _normalise_json(value: Any) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise PilotReadinessError("value must be canonical JSON") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "EvidencePin",
    "FrozenPilotProtocol",
    "PILOT_PROTOCOL_SCHEMA_VERSION",
    "PILOT_READINESS_SCHEMA_VERSION",
    "PilotReadinessError",
    "PilotReadinessReport",
    "ReceiptAttestation",
    "ReceiptSignatureVerifier",
    "ReadinessFinding",
    "freeze_pilot_protocol",
    "receipt_attestation_payload",
    "validate_pilot_execution_authorization",
    "validate_pilot_readiness",
]
