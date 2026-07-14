"""Evidence-derived claim authorization for supervisor reports."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import hmac
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Protocol
from urllib.parse import urlsplit

from .efficacy_analysis import (
    MAX_CONFIRMATION_ALPHA,
    MAX_PREREGISTERED_B_WIN_RATE,
    MIN_CONFIRMATION_POWER,
    MIN_PREREGISTERED_B_WIN_RATE,
    PilotEstimate,
    _normal_quantile,
    _wilson_interval,
    derive_confirmation_plan,
    exact_discordant_pairs_required,
    exact_mcnemar_p_value,
)
from .evidence_ledger import LedgerVerification
from .task_environment import canonical_task_identity
from .trace_graph import (
    EdgeType,
    NodeType,
    TraceEdge,
    TraceGraph,
    TraceGraphError,
    TraceIdentity,
    TraceNode,
)


CLAIM_GATE_SCHEMA_VERSION = "supervisor-claim-gate/v1"
B_VS_C_ANALYSIS_SCHEMA_VERSION = "supervisor-b-vs-c-analysis/v1"
EXPERIMENT_EVIDENCE_MANIFEST_SCHEMA_VERSION = (
    "supervisor-experiment-evidence-manifest/v1"
)
B_VS_C_ASSIGNMENTS_SCHEMA_VERSION = "supervisor-experiment-assignments/v1"
TASK_STRATA_MANIFEST_SCHEMA_VERSION = (
    "supervisor-task-strata-manifest/v1"
)
B_VS_C_GRADE_SET_SCHEMA_VERSION = "supervisor-grade-revision-set/v1"
GRADE_REVISION_SCHEMA_VERSION = "supervisor-grade-revision/v1"
TRACE_GRAPH_SCHEMA_VERSION = "supervisor-trace-graph/v1"
LEDGER_VERIFICATIONS_SCHEMA_VERSION = "supervisor-ledger-verifications/v2"
VERIFIER_MANIFEST_SCHEMA_VERSION = "supervisor-verifier-manifest/v1"
INDEPENDENT_VERIFIER_ATTESTATION_SCHEMA_VERSION = (
    "supervisor-independent-verifier-attestation/v1"
)
PILOT_CONFIRMATION_LINEAGE_SCHEMA_VERSION = (
    "supervisor-pilot-confirmation-lineage/v1"
)
EXPERIMENT_PROTOCOL_SCHEMA_VERSION = "supervisor-experiment-protocol/v1"
EXPERIMENT_ROSTER_SCHEMA_VERSION = "supervisor-experiment-roster/v1"
PILOT_ANALYSIS_SCHEMA_VERSION = "supervisor-pilot-analysis/v1"
PILOT_ASSIGNMENTS_SCHEMA_VERSION = "supervisor-pilot-assignments/v1"
PILOT_TERMINAL_OUTCOMES_SCHEMA_VERSION = (
    "supervisor-pilot-terminal-outcomes/v1"
)
STRATA_REPLICATION_ANALYSIS_SCHEMA_VERSION = (
    "supervisor-strata-replication-analysis/v1"
)
BUSINESS_VALUE_PROTOCOL_SCHEMA_VERSION = (
    "supervisor-business-value-protocol/v1"
)
INCREMENTAL_COST_PROVENANCE_SCHEMA_VERSION = (
    "supervisor-incremental-cost-provenance/v1"
)
ROI_ANALYSIS_SCHEMA_VERSION = "supervisor-roi-analysis/v2"
AUTO_IMPROVEMENT_AUTHORITY_MANIFEST_SCHEMA_VERSION = (
    "supervisor-auto-improvement-authority-manifest/v1"
)
EXTERNAL_AUTHORITY_ATTESTATION_SCHEMA_VERSION = (
    "supervisor-external-authority-attestation/v1"
)
FROZEN_CONTROL_RECEIPT_SCHEMA_VERSION = (
    "supervisor-frozen-control-receipt/v1"
)
SEALED_HOLDOUT_RECEIPT_SCHEMA_VERSION = (
    "supervisor-sealed-holdout-receipt/v1"
)
SHADOW_RESULT_SCHEMA_VERSION = "supervisor-shadow-result/v1"
HUMAN_APPROVAL_RECEIPT_SCHEMA_VERSION = (
    "supervisor-human-approval-receipt/v1"
)
CANARY_RESULT_SCHEMA_VERSION = "supervisor-canary-result/v1"
ROLLBACK_RECEIPT_SCHEMA_VERSION = "supervisor-rollback-receipt/v1"
MANAGED_CLAIM_FIELDS = (
    "improvement_claim_allowed",
    "powered_improvement_claim_allowed",
)
STRUCTURED_CLAIM_FIELD_NAMES = frozenset({
    "claim",
    "claim_text",
    "assertion",
    "asserted_claim",
    "conclusion_claim",
    "outcome_claim",
    "roi_claim",
    "auto_improvement_claim",
})
EvidenceResolver = Callable[[str], bytes | bytearray | memoryview | None]
LedgerVerificationResolver = Callable[
    [str, str],
    LedgerVerification | None,
]


class VerifierAttestationVerifier(Protocol):
    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        ...


TrustedVerifierAttestors = Mapping[
    str,
    VerifierAttestationVerifier
    | Callable[[bytes, Mapping[str, Any]], bool],
]
TrustedExternalAuthorities = Mapping[
    str,
    VerifierAttestationVerifier
    | Callable[[bytes, Mapping[str, Any]], bool],
]
_MAX_POWER_DISCORDANT_PAIRS = 10_000
_ASSIGNMENT_DERIVED_BLOCK_KEYS = frozenset({
    "assignment_method",
    "experiment_spec_hash",
    "treatment_set_hash",
    "stratum_position",
    "permuted_block_index",
    "permuted_block_position",
})
_FROZEN_ROSTER_ASSIGNMENT_METHOD = "frozen-roster-six-order/v1"
_POWERED_DESIGN_ARM_KEYS = (
    "production_baseline",
    "supervisor",
    "compute_matched_direct",
)


class ClaimLevel(str, Enum):
    """Ordered Harness v1 claim levels."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"


class ClaimGateError(ValueError):
    """Base error for claim derivation and validation failures."""


class ManualClaimFlagError(ClaimGateError):
    """Raised when a producer tries to set a ClaimGate-owned field."""


class UnsupportedClaimError(ClaimGateError):
    """Raised when report evidence cannot support an asserted claim."""


class InvalidClaimGateReceiptError(ClaimGateError):
    """Raised when a derived report is not bound to its evidence bundle."""


@dataclass(frozen=True)
class ClaimRule:
    """One registered report claim and its minimum evidence level."""

    claim_id: str
    required_level: ClaimLevel
    patterns: tuple[str, ...]


@dataclass
class _EvidenceContext:
    resolver: EvidenceResolver | None
    ledger_verification_resolver: LedgerVerificationResolver | None = None
    grade_authority: Any | None = None
    trusted_verifier_attestors: TrustedVerifierAttestors = field(
        default_factory=dict
    )
    trusted_external_authorities: TrustedExternalAuthorities = field(
        default_factory=dict
    )
    cache: dict[str, bytes | None] = field(default_factory=dict)

    def matches(self, reference: Any, expected_sha256: Any) -> bool:
        ref = str(reference or "").strip()
        expected = _normalized_sha256(expected_sha256)
        if not ref or expected is None:
            return False
        resolved = self.resolve(ref)
        return resolved is not None and sha256(resolved).hexdigest() == expected

    def resolve(self, reference: str) -> bytes | None:
        if reference in self.cache:
            return self.cache[reference]
        if self.resolver is None:
            self.cache[reference] = None
            return None
        try:
            value = self.resolver(reference)
        except Exception:
            value = None
        if isinstance(value, bytes):
            resolved = value
        elif isinstance(value, (bytearray, memoryview)):
            resolved = bytes(value)
        else:
            resolved = None
        self.cache[reference] = resolved
        return resolved

    def resolve_json(
        self,
        reference: Any,
        expected_sha256: Any,
    ) -> Any | None:
        ref = str(reference or "").strip()
        if not self.matches(ref, expected_sha256):
            return None
        resolved = self.resolve(ref)
        if resolved is None:
            return None
        try:
            return _strict_json_loads(resolved)
        except (UnicodeDecodeError, ValueError):
            return None

    def authoritative_ledger_verification(
        self,
        *,
        run_id: str,
        expected_head_hash: str,
    ) -> LedgerVerification | None:
        if self.ledger_verification_resolver is None:
            return None
        try:
            verification = self.ledger_verification_resolver(
                run_id,
                expected_head_hash,
            )
        except Exception:
            return None
        return (
            verification
            if isinstance(verification, LedgerVerification)
            else None
        )

    def verifies_external_attestation(
        self,
        *,
        evidence_kind: str,
        descriptor: Mapping[str, Any],
    ) -> bool:
        if not _is_hashed_artifact(descriptor):
            return False
        attestation = descriptor.get("attestation")
        if not isinstance(attestation, Mapping):
            return False
        authority_id = str(
            attestation.get("authority_id") or ""
        ).strip()
        signature = attestation.get("signature")
        if (
            not authority_id
            or not isinstance(signature, Mapping)
            or not signature
        ):
            return False
        verifier = self.trusted_external_authorities.get(authority_id)
        if verifier is None:
            return False
        payload = external_authority_attestation_payload(
            evidence_kind=evidence_kind,
            authority_id=authority_id,
            reference=str(descriptor.get("ref") or ""),
            sha256_digest=str(descriptor.get("sha256") or ""),
        )
        try:
            accepted = (
                verifier.verify(payload, signature)
                if hasattr(verifier, "verify")
                else verifier(payload, signature)
            )
        except Exception:
            return False
        return accepted is True


@dataclass(frozen=True)
class _AssignmentEvidence:
    task_id: str
    canonical_task_id: str
    assignment_id: str
    first_execution_started_at_ms: int


@dataclass(frozen=True)
class _GradeEvidence:
    task_id: str
    arm: str
    grade_id: str
    revision_hash: str
    run_id: str
    frozen_result_hash: str
    verifier_id: str
    verifier_version: str
    verifier_config_hash: str
    verifier_implementation_hash: str
    passed: bool
    recorded_at_ms: int
    revision_document: Mapping[str, Any]


@dataclass(frozen=True)
class _VerifierEvidence:
    verifier_id: str
    verifier_version: str
    verifier_config_hash: str
    verifier_implementation_hash: str


@dataclass(frozen=True)
class _FrozenResultEvidence:
    task_id: str
    canonical_task_id: str
    assignment_id: str
    arm: str
    run_id: str
    status: str
    frozen_result_hash: str
    passed: bool


@dataclass(frozen=True)
class _ExperimentManifestEvidence:
    roster: Mapping[str, str]
    frozen_results: Mapping[tuple[str, str], _FrozenResultEvidence]
    current_grade_revisions: frozenset[tuple[str, str, str, str]]
    verifier: _VerifierEvidence
    trace_decision_canonical_key: str
    trace_decision_revision_hash: str
    ledger_event_payloads: tuple[Mapping[str, Any], ...]


def independent_verifier_attestation_payload(
    *,
    verifier_id: str,
    producer_principal_id: str,
    verifier_principal_id: str,
    result_ref: str,
    result_sha256: str,
) -> bytes:
    """Return the canonical bytes a trusted verifier principal must sign."""
    return _canonical_json(
        {
            "schema_version": (
                INDEPENDENT_VERIFIER_ATTESTATION_SCHEMA_VERSION
            ),
            "evidence_kind": "independent_hidden_verifier",
            "verifier_id": str(verifier_id).strip(),
            "producer_principal_id": str(
                producer_principal_id
            ).strip(),
            "verifier_principal_id": str(
                verifier_principal_id
            ).strip(),
            "result_ref": str(result_ref).strip(),
            "result_sha256": str(result_sha256).strip().lower(),
            "independent": True,
            "hidden": True,
        }
    ).encode("utf-8")


def external_authority_attestation_payload(
    *,
    evidence_kind: str,
    authority_id: str,
    reference: str,
    sha256_digest: str,
) -> bytes:
    """Return canonical bytes for an external evidence authority signature."""
    return _canonical_json(
        {
            "schema_version": (
                EXTERNAL_AUTHORITY_ATTESTATION_SCHEMA_VERSION
            ),
            "evidence_kind": str(evidence_kind).strip(),
            "authority_id": str(authority_id).strip(),
            "ref": str(reference).strip(),
            "sha256": str(sha256_digest).strip().lower(),
        }
    ).encode("utf-8")


DEFAULT_CLAIM_RULES = (
    ClaimRule(
        claim_id="CLAIM-HARNESS-L0-INTEGRITY",
        required_level=ClaimLevel.L0,
        patterns=(r"\bevidence integrity is pinned\b",),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L1-PROCESS",
        required_level=ClaimLevel.L1,
        patterns=(r"\bprocess violations are traceably detected\b",),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L2-OUTCOME",
        required_level=ClaimLevel.L2,
        patterns=(r"\boutcomes pass an independent hidden verifier\b",),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L3-CAUSAL-IMPROVEMENT",
        required_level=ClaimLevel.L3,
        patterns=(
            r"\bsupervisor improves outcomes\b",
            r"\bsupervision improves outcomes\b",
            r"\bharness improves outcomes\b",
            r"\bsupervisor causes better outcomes\b",
            (
                r"\b(?:arm\s+)?b\s+"
                r"(?:produces?|yields?|delivers?|achieves?)\s+more\s+"
                r"(?:successful|passing|solved|completed)\s+"
                r"(?:tasks?|cases?|benchmarks?)\s+than\s+"
                r"(?:arm\s+)?c\b"
            ),
            (
                r"\b(?:the\s+|our\s+)?"
                r"(?:supervisor|supervision|harness)\s+"
                r"(?:produces?|yields?|delivers?|achieves?)\s+"
                r"(?:a\s+)?(?:"
                r"higher\s+(?:pass|success|solve|completion)\s+rate"
                r"|better\s+(?:benchmark\s+)?(?:outcomes?|results?)"
                r"|more\s+(?:successful|passing|solved|completed)\s+"
                r"(?:tasks?|cases?|benchmarks?)"
                r")\s+than\s+(?:the\s+)?(?:"
                r"baseline|control|direct(?:\s+execution)?"
                r"|compute[- ]matched\s+direct|arm\s+c|c"
                r")\b"
            ),
            (
                r"\b(?:the\s+|our\s+)?"
                r"(?:supervisor|harness|arm\s+b|b)\s+outperforms?\s+"
                r"(?:the\s+)?(?:baseline|control|direct(?:\s+execution)?"
                r"|compute[- ]matched\s+direct|arm\s+c|c)\s+"
                r"(?:on|for)\s+(?:benchmark\s+)?"
                r"(?:outcomes?|results?|pass\s+rate|success\s+rate)\b"
            ),
            (
                r"\b(?:the\s+|our\s+)?"
                r"(?:supervisor|harness|arm\s+b|b)\s+"
                r"(?:is|was|performs?|performed|does|did)\s+"
                r"(?:(?:statistically|significantly)\s+)?"
                r"(?:better|superior|more\s+effective)\s+than\s+"
                r"(?:the\s+)?(?:baseline|control|direct(?:\s+execution)?"
                r"|compute[- ]matched\s+direct|arm\s+c|c)\b"
            ),
            (
                r"\b(?:arm\s+)?b\s+"
                r"(?:beats?|beat|wins?\s+against)\s+"
                r"(?:arm\s+)?c\b"
            ),
        ),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L4-PORTABLE-IMPROVEMENT",
        required_level=ClaimLevel.L4,
        patterns=(
            r"\bimprovement generalizes across strata\b",
            r"\bportable improvement\b",
        ),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L5-POSITIVE-ROI",
        required_level=ClaimLevel.L5,
        patterns=(
            r"\bpositive roi\b",
            r"\bpays for itself\b",
        ),
    ),
    ClaimRule(
        claim_id="CLAIM-HARNESS-L6-SAFE-AUTO-IMPROVEMENT",
        required_level=ClaimLevel.L6,
        patterns=(
            r"\bsafe auto[- ]improvement\b",
            r"\bsafely auto[- ]improves\b",
        ),
    ),
)


class ClaimGate:
    """Derive the strongest supportable claim level from pinned evidence."""

    @classmethod
    def max_claim_level(
        cls,
        evidence_bundle: Mapping[str, Any],
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> ClaimLevel | None:
        """Return the highest level backed by resolved, byte-matching evidence.

        ``evidence_root`` resolves file references within one trusted root.
        ``evidence_resolver`` is an alternative adapter from reference to bytes.
        Without either, or when any required reference cannot be resolved or
        hashed to its declared SHA-256, evaluation fails closed.
        """
        context = _evidence_context(
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        return cls._max_claim_level(evidence_bundle, context=context)

    @classmethod
    def _max_claim_level(
        cls,
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> ClaimLevel | None:
        if not cls._has_integrity_evidence(evidence_bundle, context=context):
            return None
        if not cls._has_traceable_detector(evidence_bundle, context=context):
            return ClaimLevel.L0
        if not cls._has_independent_hidden_verifier(
            evidence_bundle,
            context=context,
        ):
            return ClaimLevel.L1
        if not cls._has_randomized_powered_b_vs_c(
            evidence_bundle,
            context=context,
        ):
            return ClaimLevel.L2
        if not cls._has_strata_replication(evidence_bundle, context=context):
            return ClaimLevel.L3
        if not cls._has_operating_cost_and_positive_roi(
            evidence_bundle,
            context=context,
        ):
            return ClaimLevel.L4
        if not cls._has_auto_improvement_controls(
            evidence_bundle,
            context=context,
        ):
            return ClaimLevel.L5
        return ClaimLevel.L6

    @classmethod
    def derived_claim_flags(
        cls,
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> dict[str, bool]:
        level = cls.max_claim_level(
            evidence_bundle or {},
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        return _derived_claim_flags(level)

    @classmethod
    def derive_report(
        cls,
        report: Mapping[str, Any],
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> dict[str, Any]:
        report_body = cls._unwrap_default_fail_closed_report(report)
        _reject_managed_claim_fields(report_body)
        evidence = dict(evidence_bundle or {})
        context = _evidence_context(
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        level = cls._max_claim_level(evidence, context=context)
        _validate_report_for_level(report_body, actual_level=level)
        return {
            **report_body,
            **_derived_claim_flags(level),
            "claim_gate": {
                "schema_version": CLAIM_GATE_SCHEMA_VERSION,
                "max_claim_level": level.value if level is not None else None,
                "evidence_bundle_sha256": _evidence_bundle_sha256(evidence),
                "derived_fields": list(MANAGED_CLAIM_FIELDS),
                "managed_field_paths": list(MANAGED_CLAIM_FIELDS),
            },
        }

    @classmethod
    def govern_report(
        cls,
        report: Mapping[str, Any],
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> dict[str, Any]:
        """Attach a verifiable receipt to a legacy report shape.

        Older benchmark schemas repeat ClaimGate-owned flags in nested
        comparison and authority blocks.  Rewriting those public schemas in
        one migration would destroy replay compatibility, so this boundary
        verifies every existing occurrence against recomputed authority,
        ensures the canonical top-level fields exist, and records the exact
        managed paths in the receipt.  It never accepts a caller-selected
        value.

        New report producers should use :meth:`derive_report`, whose stricter
        contract rejects all pre-existing managed fields.
        """
        evidence = dict(evidence_bundle or {})
        context = _evidence_context(
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        level = cls._max_claim_level(evidence, context=context)
        expected_flags = _derived_claim_flags(level)
        governed = dict(report)
        governed.pop("claim_gate", None)
        for path, value in _managed_claim_field_values(governed):
            field_name = path.rsplit(".", 1)[-1]
            expected = expected_flags[field_name]
            if value is not expected:
                raise ManualClaimFlagError(
                    f"{path} does not match ClaimGate-derived authority"
                )
        for field_name, expected in expected_flags.items():
            governed[field_name] = expected

        _validate_report_for_level(
            _without_managed_claim_fields(governed),
            actual_level=level,
        )
        managed_paths = [
            path for path, _value in _managed_claim_field_values(governed)
        ]
        governed["claim_gate"] = {
            "schema_version": CLAIM_GATE_SCHEMA_VERSION,
            "max_claim_level": level.value if level is not None else None,
            "evidence_bundle_sha256": _evidence_bundle_sha256(evidence),
            "derived_fields": list(MANAGED_CLAIM_FIELDS),
            "managed_field_paths": managed_paths,
        }
        return governed

    @classmethod
    def _unwrap_default_fail_closed_report(
        cls,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Permit one safe transition from the default receipt to real evidence.

        AutoResearch reports are born with an empty-evidence receipt so their
        lack of authority is explicit.  A trusted later stage may replace that
        receipt only after it verifies as the exact empty-evidence,
        fail-closed state.  Bare/manual fields, forged receipts, nested managed
        fields, and already-authoritative receipts remain hard errors.
        """
        managed_paths = _managed_claim_field_paths(report)
        if not managed_paths and "claim_gate" not in report:
            return dict(report)
        if (
            set(managed_paths) != set(MANAGED_CLAIM_FIELDS)
            or not isinstance(report.get("claim_gate"), Mapping)
        ):
            _reject_managed_claim_fields(report)
            raise ManualClaimFlagError(
                "claim_gate receipt is derived by ClaimGate"
            )
        try:
            previous_level = cls.validate_derived_report(report, {})
        except ClaimGateError as exc:
            raise ManualClaimFlagError(
                f"{managed_paths[0]} is derived by ClaimGate"
            ) from exc
        if previous_level is not None:
            raise ManualClaimFlagError(
                f"{managed_paths[0]} is derived by ClaimGate"
            )
        report_body = dict(report)
        report_body.pop("claim_gate", None)
        for field_name in MANAGED_CLAIM_FIELDS:
            report_body.pop(field_name, None)
        return report_body

    @classmethod
    def validate_derived_report(
        cls,
        report: Mapping[str, Any],
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> ClaimLevel | None:
        """Recompute and verify a report's embedded ClaimGate receipt."""
        evidence = dict(evidence_bundle or {})
        context = _evidence_context(
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        actual_level = cls._max_claim_level(evidence, context=context)
        expected_flags = _derived_claim_flags(actual_level)
        managed_values = _managed_claim_field_values(report)
        managed_paths = [path for path, _value in managed_values]
        receipt = report.get("claim_gate")
        declared_paths = (
            receipt.get("managed_field_paths")
            if isinstance(receipt, Mapping)
            else None
        )
        if declared_paths is None:
            declared_paths = list(MANAGED_CLAIM_FIELDS)
        if (
            not isinstance(declared_paths, list)
            or managed_paths != [str(path) for path in declared_paths]
        ):
            raise InvalidClaimGateReceiptError(
                "ClaimGate-managed field paths do not match the receipt"
            )
        if not all(field_name in managed_paths for field_name in MANAGED_CLAIM_FIELDS):
            raise InvalidClaimGateReceiptError(
                "derived report must contain the top-level ClaimGate-managed "
                "fields"
            )
        for path, value in managed_values:
            field_name = path.rsplit(".", 1)[-1]
            expected_value = expected_flags[field_name]
            if value is not expected_value:
                raise InvalidClaimGateReceiptError(
                    f"{path} does not match recomputed ClaimGate authority"
                )

        if not isinstance(receipt, Mapping):
            raise InvalidClaimGateReceiptError(
                "derived report is missing its ClaimGate receipt"
            )
        if receipt.get("schema_version") != CLAIM_GATE_SCHEMA_VERSION:
            raise InvalidClaimGateReceiptError(
                "ClaimGate receipt schema_version is invalid"
            )
        expected_level = (
            actual_level.value if actual_level is not None else None
        )
        if receipt.get("max_claim_level") != expected_level:
            raise InvalidClaimGateReceiptError(
                "ClaimGate receipt max_claim_level does not match "
                "recomputed evidence support"
            )
        expected_evidence_hash = _evidence_bundle_sha256(evidence)
        declared_evidence_hash = _normalized_sha256(
            receipt.get("evidence_bundle_sha256")
        )
        if declared_evidence_hash is None or not hmac.compare_digest(
            declared_evidence_hash,
            expected_evidence_hash,
        ):
            raise InvalidClaimGateReceiptError(
                "ClaimGate receipt evidence bundle hash does not match "
                "the bound evidence bundle"
            )
        if receipt.get("derived_fields") != list(MANAGED_CLAIM_FIELDS):
            raise InvalidClaimGateReceiptError(
                "ClaimGate receipt derived_fields do not match managed fields"
            )

        untrusted_report = dict(report)
        untrusted_report.pop("claim_gate", None)
        _validate_report_for_level(
            _without_managed_claim_fields(untrusted_report),
            actual_level=actual_level,
        )
        return actual_level

    @classmethod
    def validate_report(
        cls,
        report: Mapping[str, Any],
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        ledger_verification_resolver: (
            LedgerVerificationResolver | None
        ) = None,
        grade_authority: Any | None = None,
        trusted_verifier_attestors: (
            TrustedVerifierAttestors | None
        ) = None,
        trusted_external_authorities: (
            TrustedExternalAuthorities | None
        ) = None,
    ) -> ClaimLevel | None:
        _reject_managed_claim_fields(report)
        evidence = dict(evidence_bundle or {})
        actual_level = cls.max_claim_level(
            evidence,
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
        _validate_report_for_level(report, actual_level=actual_level)
        return actual_level

    @staticmethod
    def _has_integrity_evidence(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        pins = evidence_bundle.get("pins")
        hashes = evidence_bundle.get("hashes")
        artifacts = evidence_bundle.get("artifacts")
        if not (
            isinstance(pins, Mapping)
            and bool(pins)
            and all(str(value).strip() for value in pins.values())
            and isinstance(hashes, Mapping)
            and bool(hashes)
            and isinstance(artifacts, Sequence)
            and not isinstance(artifacts, (str, bytes, bytearray))
            and bool(artifacts)
        ):
            return False
        verified_hashes: set[str] = set()
        for artifact in artifacts:
            if not _is_hashed_artifact(artifact):
                return False
            if not context.matches(artifact.get("ref"), artifact.get("sha256")):
                return False
            artifact_hash = _normalized_sha256(artifact.get("sha256"))
            if artifact_hash is None:
                return False
            verified_hashes.add(artifact_hash)
        declared_hashes = [
            _normalized_sha256(value)
            for value in hashes.values()
        ]
        return all(
            declared_hash is not None
            and declared_hash in verified_hashes
            for declared_hash in declared_hashes
        )

    @staticmethod
    def _has_traceable_detector(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        detector = evidence_bundle.get("traceable_detector")
        return (
            isinstance(detector, Mapping)
            and bool(str(detector.get("detector_id") or "").strip())
            and context.matches(
                detector.get("trace_ref"),
                detector.get("trace_sha256"),
            )
        )

    @staticmethod
    def _has_independent_hidden_verifier(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        verifier = evidence_bundle.get("independent_hidden_verifier")
        if not isinstance(verifier, Mapping):
            return False
        verifier_id = str(verifier.get("verifier_id") or "").strip()
        producer_principal_id = str(
            verifier.get("producer_principal_id") or ""
        ).strip()
        attestation = verifier.get("attestation")
        if not isinstance(attestation, Mapping):
            return False
        verifier_principal_id = str(
            attestation.get("verifier_principal_id") or ""
        ).strip()
        signature = attestation.get("signature")
        if (
            verifier.get("independent") is not True
            or verifier.get("hidden") is not True
            or not verifier_id
            or not producer_principal_id
            or not verifier_principal_id
            or producer_principal_id.casefold()
            == verifier_principal_id.casefold()
            or not isinstance(signature, Mapping)
            or not signature
            or not context.matches(
                verifier.get("result_ref"),
                verifier.get("result_sha256"),
            )
        ):
            return False
        trusted_verifier = context.trusted_verifier_attestors.get(
            verifier_principal_id
        )
        if trusted_verifier is None:
            return False
        payload = independent_verifier_attestation_payload(
            verifier_id=verifier_id,
            producer_principal_id=producer_principal_id,
            verifier_principal_id=verifier_principal_id,
            result_ref=str(verifier.get("result_ref") or ""),
            result_sha256=str(verifier.get("result_sha256") or ""),
        )
        try:
            accepted = (
                trusted_verifier.verify(payload, signature)
                if hasattr(trusted_verifier, "verify")
                else trusted_verifier(payload, signature)
            )
        except Exception:
            return False
        return accepted is True

    @staticmethod
    def _has_randomized_powered_b_vs_c(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        result = evidence_bundle.get("randomized_powered_b_vs_c")
        comparison = (
            str(result.get("comparison") or "")
            if isinstance(result, Mapping)
            else ""
        )
        normalized_comparison = (
            comparison.lower().replace("-", "_").replace(" ", "_")
        )
        analysis = (
            context.resolve_json(
                result.get("analysis_ref"),
                result.get("analysis_sha256"),
            )
            if isinstance(result, Mapping)
            else None
        )
        manifest_descriptor = (
            result.get("experiment_manifest")
            if isinstance(result, Mapping)
            else None
        )
        manifest = (
            context.resolve_json(
                manifest_descriptor.get("ref"),
                manifest_descriptor.get("sha256"),
            )
            if (
                isinstance(manifest_descriptor, Mapping)
                and context.verifies_external_attestation(
                    evidence_kind="experiment_evidence_manifest",
                    descriptor=manifest_descriptor,
                )
            )
            else None
        )
        return (
            isinstance(result, Mapping)
            and normalized_comparison == "b_vs_c"
            and result.get("randomized") is True
            and result.get("powered") is True
            and result.get("supports_improvement") is True
            and isinstance(analysis, Mapping)
            and isinstance(manifest, Mapping)
            and _validate_b_vs_c_analysis(
                analysis,
                analysis_descriptor={
                    "ref": result.get("analysis_ref"),
                    "sha256": result.get("analysis_sha256"),
                },
                manifest=manifest,
                context=context,
            )
        )

    @staticmethod
    def _has_strata_replication(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        replication = evidence_bundle.get("strata_replication")
        analysis = (
            context.resolve_json(
                replication.get("analysis_ref"),
                replication.get("analysis_sha256"),
            )
            if isinstance(replication, Mapping)
            else None
        )
        causal_evidence = evidence_bundle.get("randomized_powered_b_vs_c")
        causal_analysis = (
            context.resolve_json(
                causal_evidence.get("analysis_ref"),
                causal_evidence.get("analysis_sha256"),
            )
            if isinstance(causal_evidence, Mapping)
            else None
        )
        return (
            isinstance(replication, Mapping)
            and replication.get("replicated") is True
            and isinstance(analysis, Mapping)
            and isinstance(causal_analysis, Mapping)
            and _validate_strata_replication_analysis(
                analysis,
                source_causal_analysis=causal_analysis,
                source_causal_analysis_sha256=(
                    causal_evidence.get("analysis_sha256")
                    if isinstance(causal_evidence, Mapping)
                    else None
                ),
                declared_strata=replication.get("strata"),
                declared_model_families=replication.get(
                    "model_families"
                ),
                context=context,
            )
        )

    @staticmethod
    def _has_operating_cost_and_positive_roi(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        operating_cost = evidence_bundle.get("operating_cost")
        raw_cost = (
            operating_cost.get("cost_usd")
            if isinstance(operating_cost, Mapping)
            else None
        )
        roi_analysis = (
            context.resolve_json(
                operating_cost.get("analysis_ref"),
                operating_cost.get("analysis_sha256"),
            )
            if isinstance(operating_cost, Mapping)
            else None
        )
        causal_evidence = evidence_bundle.get("randomized_powered_b_vs_c")
        causal_analysis = (
            context.resolve_json(
                causal_evidence.get("analysis_ref"),
                causal_evidence.get("analysis_sha256"),
            )
            if isinstance(causal_evidence, Mapping)
            else None
        )
        roi_authority_descriptor = (
            {
                "ref": operating_cost.get("analysis_ref"),
                "sha256": operating_cost.get("analysis_sha256"),
                "attestation": operating_cost.get("attestation"),
            }
            if isinstance(operating_cost, Mapping)
            else {}
        )
        return (
            isinstance(operating_cost, Mapping)
            and operating_cost.get("measured") is True
            and operating_cost.get("supports_positive_roi") is True
            and context.verifies_external_attestation(
                evidence_kind="roi_analysis",
                descriptor=roi_authority_descriptor,
            )
            and isinstance(roi_analysis, Mapping)
            and isinstance(causal_analysis, Mapping)
            and _validate_positive_roi_analysis(
                roi_analysis,
                operating_cost_usd=raw_cost,
                causal_analysis=causal_analysis,
                causal_analysis_sha256=(
                    causal_evidence.get("analysis_sha256")
                    if isinstance(causal_evidence, Mapping)
                    else None
                ),
                context=context,
            )
        )

    @staticmethod
    def _has_auto_improvement_controls(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        authority_descriptor = evidence_bundle.get(
            "auto_improvement_authority_manifest"
        )
        authority_manifest = (
            context.resolve_json(
                authority_descriptor.get("ref"),
                authority_descriptor.get("sha256"),
            )
            if (
                isinstance(authority_descriptor, Mapping)
                and context.verifies_external_attestation(
                    evidence_kind="auto_improvement_authority_manifest",
                    descriptor=authority_descriptor,
                )
            )
            else None
        )
        if not isinstance(authority_manifest, Mapping):
            return False
        receipt_specs = (
            ("frozen_control", "frozen"),
            ("sealed_holdout", "sealed"),
            ("shadow_result", "passed"),
            ("human_approval", "approved"),
            ("canary", "passed"),
            ("rollback_receipt", "passed"),
        )
        descriptors: dict[str, Mapping[str, Any]] = {}
        receipts: dict[str, Mapping[str, Any]] = {}
        for key, state_key in receipt_specs:
            descriptor = evidence_bundle.get(key)
            if (
                not isinstance(descriptor, Mapping)
                or descriptor.get(state_key) is not True
            ):
                return False
            receipt = context.resolve_json(
                descriptor.get("ref"),
                descriptor.get("sha256"),
            )
            if not isinstance(receipt, Mapping):
                return False
            descriptors[key] = descriptor
            receipts[key] = receipt
        return (
            _validate_auto_improvement_authority_manifest(
                authority_manifest,
                descriptors=descriptors,
                receipts=receipts,
            )
            and _validate_auto_improvement_receipts(
                receipts,
                descriptors=descriptors,
            )
        )


def _validate_b_vs_c_analysis(
    analysis: Mapping[str, Any],
    *,
    analysis_descriptor: Mapping[str, Any],
    manifest: Mapping[str, Any],
    context: _EvidenceContext,
) -> bool:
    try:
        if (
            analysis.get("schema_version")
            != B_VS_C_ANALYSIS_SCHEMA_VERSION
        ):
            return False
        experiment_id = _required_text(
            analysis.get("experiment_id"),
            field="experiment_id",
        )
        if _normalized_comparison(analysis.get("comparison")) != "b_vs_c":
            return False
        design = _required_mapping(analysis.get("design"), field="design")
        task_rows = _required_sequence(
            analysis.get("task_rows"),
            field="task_rows",
        )
        result = _required_mapping(analysis.get("result"), field="result")
        lineage = _required_mapping(
            analysis.get("lineage"),
            field="lineage",
        )

        descriptors: dict[str, Mapping[str, Any]] = {}
        documents: dict[str, Mapping[str, Any]] = {}
        for name in ("assignments", "grades", "trace", "ledger", "verifier"):
            descriptor = _required_mapping(
                lineage.get(name),
                field=f"lineage.{name}",
            )
            if not _is_hashed_artifact(descriptor):
                return False
            document = context.resolve_json(
                descriptor.get("ref"),
                descriptor.get("sha256"),
            )
            if not isinstance(document, Mapping):
                return False
            descriptors[name] = descriptor
            documents[name] = document

        study_descriptor = _required_mapping(
            lineage.get("study"),
            field="lineage.study",
        )
        if not _is_hashed_artifact(study_descriptor):
            return False
        study = context.resolve_json(
            study_descriptor.get("ref"),
            study_descriptor.get("sha256"),
        )
        descriptors["study"] = study_descriptor
        manifest_evidence = _parse_experiment_evidence_manifest(
            manifest,
            experiment_id=experiment_id,
            analysis=analysis,
            analysis_descriptor=analysis_descriptor,
            descriptors=descriptors,
            context=context,
        )
        assignments = _parse_assignment_evidence(
            documents["assignments"],
            experiment_id=experiment_id,
            powered_design=design,
        )
        if (
            set(assignments) != set(manifest_evidence.roster)
            or any(
                assignment.canonical_task_id
                != manifest_evidence.roster[task_id]
                for task_id, assignment in assignments.items()
            )
        ):
            return False
        if (
            not isinstance(study, Mapping)
            or not _validate_pilot_confirmation_lineage(
                study,
                experiment_id=experiment_id,
                design=design,
                assignments=assignments,
                confirmation_roster=manifest_evidence.roster,
                context=context,
            )
        ):
            return False
        grades = _parse_grade_evidence(
            documents["grades"],
            experiment_id=experiment_id,
        )
        if not _grades_are_current_in_authoritative_gradebook(
            grades,
            grade_authority=context.grade_authority,
        ):
            return False
        verifier = _parse_verifier_evidence(documents["verifier"])
        if verifier != manifest_evidence.verifier:
            return False
        if {
            (
                grade.task_id,
                grade.arm,
                grade.grade_id,
                grade.revision_hash,
            )
            for grade in grades.values()
        } != set(manifest_evidence.current_grade_revisions):
            return False
        if not _ledger_covers_grade_runs(
            documents["ledger"],
            experiment_id=experiment_id,
            assignment_artifact_sha256=descriptors["assignments"].get(
                "sha256"
            ),
            grade_artifact_sha256=descriptors["grades"].get("sha256"),
            grades=grades,
            assignments=assignments,
            expected_event_payloads=(
                manifest_evidence.ledger_event_payloads
            ),
            context=context,
        ):
            return False
        if not _trace_covers_analysis_lineage(
            documents["trace"],
            experiment_id=experiment_id,
            assignments=assignments,
            grades=grades,
            verifier=verifier,
            expected_decision_canonical_key=(
                manifest_evidence.trace_decision_canonical_key
            ),
            expected_decision_revision_hash=(
                manifest_evidence.trace_decision_revision_hash
            ),
            grade_authority=context.grade_authority,
        ):
            return False

        paired_rows = _validate_paired_task_rows(
            task_rows,
            assignments=assignments,
            grades=grades,
            verifier=verifier,
            frozen_results=manifest_evidence.frozen_results,
        )
        if paired_rows is None:
            return False
        counts = _paired_counts(paired_rows)
        if not _validate_powered_design(
            design,
            task_count=len(paired_rows),
            discordant_pairs=counts["discordant_pairs"],
        ):
            return False
        return _validate_positive_paired_result(
            result,
            counts=counts,
            alpha=_required_number(
                _required_mapping(
                    design.get("power"),
                    field="design.power",
                ).get("alpha"),
                field="design.power.alpha",
            ),
        )
    except (KeyError, OverflowError, TypeError, ValueError):
        return False


def _validate_strata_replication_analysis(
    analysis: Mapping[str, Any],
    *,
    source_causal_analysis: Mapping[str, Any],
    source_causal_analysis_sha256: Any,
    declared_strata: Any,
    declared_model_families: Any,
    context: _EvidenceContext,
) -> bool:
    try:
        if (
            analysis.get("schema_version")
            != STRATA_REPLICATION_ANALYSIS_SCHEMA_VERSION
            or _normalized_sha256(
                analysis.get("source_causal_analysis_sha256")
            )
            != _normalized_sha256(source_causal_analysis_sha256)
        ):
            return False
        source_experiment_id = _required_text(
            source_causal_analysis.get("experiment_id"),
            field="source causal experiment_id",
        )
        source_task_ids = _analysis_task_ids(source_causal_analysis)
        raw_studies = _required_sequence(
            analysis.get("studies"),
            field="replication studies",
        )
        result = _required_mapping(
            analysis.get("result"),
            field="replication result",
        )
        if len(raw_studies) < 3 or result.get("replicated") is not True:
            return False

        study_hashes: set[str] = set()
        experiment_ids: set[str] = set()
        all_task_ids = set(source_task_ids)
        strata: set[str] = set()
        family_optimizer_visibility: dict[str, bool] = {}
        for raw_study in raw_studies:
            descriptor = _required_mapping(
                raw_study,
                field="replication study",
            )
            study_hash = _normalized_sha256(
                descriptor.get("analysis_sha256")
            )
            if (
                study_hash is None
                or study_hash in study_hashes
                or study_hash
                == _normalized_sha256(source_causal_analysis_sha256)
            ):
                return False
            study_analysis = context.resolve_json(
                descriptor.get("analysis_ref"),
                study_hash,
            )
            study_manifest_descriptor = descriptor.get(
                "experiment_manifest"
            )
            study_manifest = (
                context.resolve_json(
                    study_manifest_descriptor.get("ref"),
                    study_manifest_descriptor.get("sha256"),
                )
                if (
                    isinstance(study_manifest_descriptor, Mapping)
                    and context.verifies_external_attestation(
                        evidence_kind="experiment_evidence_manifest",
                        descriptor=study_manifest_descriptor,
                    )
                )
                else None
            )
            if (
                not isinstance(study_analysis, Mapping)
                or not isinstance(study_manifest, Mapping)
                or not _validate_b_vs_c_analysis(
                    study_analysis,
                    analysis_descriptor={
                        "ref": descriptor.get("analysis_ref"),
                        "sha256": study_hash,
                    },
                    manifest=study_manifest,
                    context=context,
                )
            ):
                return False
            experiment_id = _required_text(
                study_analysis.get("experiment_id"),
                field="replication experiment_id",
            )
            if (
                experiment_id == source_experiment_id
                or experiment_id in experiment_ids
            ):
                return False
            task_ids = _analysis_task_ids(study_analysis)
            if all_task_ids.intersection(task_ids):
                return False
            replication_context = _required_mapping(
                study_analysis.get("replication_context"),
                field="replication context",
            )
            stratum = _normalized_token(
                _required_text(
                    replication_context.get("stratum"),
                    field="replication stratum",
                )
            )
            family = _model_family_name(
                replication_context.get("model_family")
            )
            model_version = _required_text(
                replication_context.get("model_version"),
                field="replication model_version",
            )
            optimizer_seen = replication_context.get("seen_by_optimizer")
            if (
                not stratum
                or not family
                or not _is_pinned_model_version(model_version)
                or not isinstance(optimizer_seen, bool)
            ):
                return False
            if (
                family in family_optimizer_visibility
                and family_optimizer_visibility[family] is not optimizer_seen
            ):
                return False
            study_hashes.add(study_hash)
            experiment_ids.add(experiment_id)
            all_task_ids.update(task_ids)
            strata.add(stratum)
            family_optimizer_visibility[family] = optimizer_seen

        declared_strata_set = _normalized_text_set(
            declared_strata,
            field="declared replication strata",
        )
        result_strata = _normalized_text_set(
            result.get("strata"),
            field="replication result strata",
        )
        declared_families = _replication_family_declarations(
            declared_model_families,
            field="declared replication model families",
        )
        result_families = _replication_family_declarations(
            result.get("model_families"),
            field="replication result model families",
        )
        return (
            len(strata) >= 2
            and len(family_optimizer_visibility) >= 3
            and any(
                seen_by_optimizer is False
                for seen_by_optimizer in family_optimizer_visibility.values()
            )
            and _required_positive_int(
                result.get("study_count"),
                field="replication study_count",
            )
            == len(raw_studies)
            and declared_strata_set == strata
            and result_strata == strata
            and declared_families == family_optimizer_visibility
            and result_families == family_optimizer_visibility
        )
    except (KeyError, TypeError, ValueError):
        return False


def _parse_experiment_evidence_manifest(
    manifest: Mapping[str, Any],
    *,
    experiment_id: str,
    analysis: Mapping[str, Any],
    analysis_descriptor: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    context: _EvidenceContext,
) -> _ExperimentManifestEvidence:
    if (
        manifest.get("schema_version")
        != EXPERIMENT_EVIDENCE_MANIFEST_SCHEMA_VERSION
        or _required_text(
            manifest.get("experiment_id"),
            field="manifest experiment_id",
        )
        != experiment_id
    ):
        raise ValueError("experiment evidence manifest identity mismatch")

    artifact_bindings = _required_mapping(
        manifest.get("artifacts"),
        field="manifest artifacts",
    )
    expected_artifact_names = {
        "analysis",
        "assignments",
        "grades",
        "trace",
        "ledger",
        "verifier",
        "study",
    }
    if set(artifact_bindings) != expected_artifact_names:
        raise ValueError(
            "manifest must bind the complete experiment artifact set"
        )
    expected_descriptors = {
        **descriptors,
        "analysis": analysis_descriptor,
    }
    for name in expected_artifact_names:
        bound = _required_mapping(
            artifact_bindings.get(name),
            field=f"manifest artifact {name}",
        )
        expected = expected_descriptors.get(name)
        if (
            expected is None
            or not _is_hashed_artifact(bound)
            or str(bound.get("ref") or "").strip()
            != str(expected.get("ref") or "").strip()
            or _normalized_sha256(bound.get("sha256"))
            != _normalized_sha256(expected.get("sha256"))
        ):
            raise ValueError(
                f"manifest artifact binding mismatch for {name}"
            )

    analysis_hashes = _required_mapping(
        manifest.get("analysis_hashes"),
        field="manifest analysis_hashes",
    )
    if (
        _normalized_sha256(analysis_hashes.get("analysis_sha256"))
        != _normalized_sha256(analysis_descriptor.get("sha256"))
        or _normalized_sha256(analysis_hashes.get("design_sha256"))
        != _sha256_canonical_json(
            _required_mapping(analysis.get("design"), field="design")
        )
        or _normalized_sha256(analysis_hashes.get("task_rows_sha256"))
        != _sha256_canonical_json(
            _required_sequence(
                analysis.get("task_rows"),
                field="task_rows",
            )
        )
        or _normalized_sha256(analysis_hashes.get("result_sha256"))
        != _sha256_canonical_json(
            _required_mapping(analysis.get("result"), field="result")
        )
    ):
        raise ValueError("manifest analysis hashes do not match analysis")

    roster_records = _required_sequence(
        manifest.get("frozen_roster"),
        field="manifest frozen_roster",
    )
    roster: dict[str, str] = {}
    canonical_ids: set[str] = set()
    for raw_record in roster_records:
        record = _required_mapping(
            raw_record,
            field="manifest roster task",
        )
        task_id = _required_text(
            record.get("task_id"),
            field="manifest roster task_id",
        )
        task_identity = _required_mapping(
            record.get("task_identity"),
            field="manifest roster task_identity",
        )
        canonical_task_id = canonical_task_identity(task_identity)
        if (
            _normalized_sha256(record.get("canonical_task_id"))
            != canonical_task_id
            or task_id in roster
            or canonical_task_id in canonical_ids
        ):
            raise ValueError(
                "manifest roster contains a duplicate or aliased task"
            )
        roster[task_id] = canonical_task_id
        canonical_ids.add(canonical_task_id)
    if not roster:
        raise ValueError("manifest frozen_roster is empty")

    raw_frozen_results = _required_sequence(
        manifest.get("frozen_results"),
        field="manifest frozen_results",
    )
    frozen_results: dict[
        tuple[str, str],
        _FrozenResultEvidence,
    ] = {}
    terminal_statuses = {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    }
    for raw_result in raw_frozen_results:
        record = _required_mapping(
            raw_result,
            field="manifest frozen result",
        )
        task_id = _required_text(
            record.get("task_id"),
            field="manifest result task_id",
        )
        arm = _required_text(
            record.get("arm"),
            field="manifest result arm",
        )
        key = (task_id, arm)
        status = _normalized_token(record.get("status"))
        passed = record.get("passed")
        if (
            task_id not in roster
            or arm not in {"supervisor", "compute_matched_direct"}
            or key in frozen_results
            or _normalized_sha256(record.get("canonical_task_id"))
            != roster[task_id]
            or status not in terminal_statuses
            or not isinstance(passed, bool)
            or (status != "completed" and passed)
        ):
            raise ValueError("manifest frozen result is invalid")
        frozen_results[key] = _FrozenResultEvidence(
            task_id=task_id,
            canonical_task_id=roster[task_id],
            assignment_id=_required_sha256(
                record.get("assignment_id"),
                field="manifest result assignment_id",
            ),
            arm=arm,
            run_id=_required_text(
                record.get("run_id"),
                field="manifest result run_id",
            ),
            status=status,
            frozen_result_hash=_required_sha256(
                record.get("frozen_result_hash"),
                field="manifest result frozen_result_hash",
            ),
            passed=passed,
        )
    expected_result_keys = {
        (task_id, arm)
        for task_id in roster
        for arm in ("supervisor", "compute_matched_direct")
    }
    if set(frozen_results) != expected_result_keys:
        raise ValueError(
            "manifest frozen results must contain complete B/C ITT outcomes"
        )

    raw_current_grades = _required_sequence(
        manifest.get("current_grade_revisions"),
        field="manifest current_grade_revisions",
    )
    current_grades: set[tuple[str, str, str, str]] = set()
    for raw_grade in raw_current_grades:
        record = _required_mapping(
            raw_grade,
            field="manifest current grade",
        )
        key = (
            _required_text(
                record.get("task_id"),
                field="manifest grade task_id",
            ),
            _required_text(
                record.get("arm"),
                field="manifest grade arm",
            ),
            _required_text(
                record.get("grade_id"),
                field="manifest grade_id",
            ),
            _required_sha256(
                record.get("revision_hash"),
                field="manifest grade revision_hash",
            ),
        )
        if key in current_grades:
            raise ValueError("manifest contains duplicate current grades")
        current_grades.add(key)
    if len(current_grades) != len(expected_result_keys):
        raise ValueError("manifest current grade set is incomplete")

    verifier_record = _required_mapping(
        manifest.get("verifier_implementation"),
        field="manifest verifier_implementation",
    )
    verifier = _VerifierEvidence(
        verifier_id=_required_text(
            verifier_record.get("verifier_id"),
            field="manifest verifier_id",
        ),
        verifier_version=_required_text(
            verifier_record.get("verifier_version"),
            field="manifest verifier_version",
        ),
        verifier_config_hash=_required_sha256(
            verifier_record.get("verifier_config_hash"),
            field="manifest verifier config hash",
        ),
        verifier_implementation_hash=_required_sha256(
            verifier_record.get("verifier_implementation_hash"),
            field="manifest verifier implementation hash",
        ),
    )

    trace_decision = _required_mapping(
        manifest.get("trace_decision"),
        field="manifest trace_decision",
    )
    trace_decision_canonical_key = _required_text(
        trace_decision.get("canonical_key"),
        field="manifest trace decision canonical_key",
    )
    trace_decision_revision_hash = _required_sha256(
        trace_decision.get("revision_hash"),
        field="manifest trace decision revision_hash",
    )

    raw_event_payloads = _required_sequence(
        manifest.get("ledger_event_payloads"),
        field="manifest ledger_event_payloads",
    )
    event_payloads: list[Mapping[str, Any]] = []
    event_keys: set[tuple[str, str]] = set()
    for raw_event in raw_event_payloads:
        event = _required_mapping(
            raw_event,
            field="manifest ledger event payload",
        )
        run_id = _required_text(
            event.get("run_id"),
            field="manifest event run_id",
        )
        event_id = _required_text(
            event.get("event_id"),
            field="manifest event_id",
        )
        if (
            (run_id, event_id) in event_keys
            or not _is_hashed_artifact(event)
            or not context.matches(event.get("ref"), event.get("sha256"))
        ):
            raise ValueError("manifest ledger event payload is invalid")
        event_keys.add((run_id, event_id))
        event_payloads.append(dict(event))
    if len(event_payloads) != len(expected_result_keys):
        raise ValueError(
            "manifest must bind one exact ledger head payload per B/C run"
        )

    return _ExperimentManifestEvidence(
        roster=roster,
        frozen_results=frozen_results,
        current_grade_revisions=frozenset(current_grades),
        verifier=verifier,
        trace_decision_canonical_key=trace_decision_canonical_key,
        trace_decision_revision_hash=trace_decision_revision_hash,
        ledger_event_payloads=tuple(event_payloads),
    )


def _analysis_task_ids(analysis: Mapping[str, Any]) -> set[str]:
    rows = _required_sequence(
        analysis.get("task_rows"),
        field="analysis task_rows",
    )
    task_ids: set[str] = set()
    for raw_row in rows:
        row = _required_mapping(raw_row, field="analysis task row")
        task_id = _required_sha256(
            row.get("canonical_task_id"),
            field="analysis canonical_task_id",
        )
        if task_id in task_ids:
            raise ValueError("duplicate analysis task_id")
        task_ids.add(task_id)
    if not task_ids:
        raise ValueError("analysis task_rows must be non-empty")
    return task_ids


def _normalized_text_set(value: Any, *, field: str) -> set[str]:
    items = _required_sequence(value, field=field)
    normalized = {
        _normalized_token(_required_text(item, field=field))
        for item in items
    }
    if not normalized or "" in normalized or len(normalized) != len(items):
        raise ValueError(f"{field} must contain distinct non-empty values")
    return normalized


def _replication_family_declarations(
    value: Any,
    *,
    field: str,
) -> dict[str, bool]:
    records = _required_sequence(value, field=field)
    families: dict[str, bool] = {}
    for raw_record in records:
        record = _required_mapping(raw_record, field=field)
        family = _model_family_name(record.get("family"))
        optimizer_seen = record.get("seen_by_optimizer")
        if (
            not family
            or family in families
            or record.get("pinned") is not True
            or not isinstance(optimizer_seen, bool)
        ):
            raise ValueError(f"{field} contains an invalid family record")
        families[family] = optimizer_seen
    if not families:
        raise ValueError(f"{field} must be non-empty")
    return families


def _is_pinned_model_version(value: Any) -> bool:
    text = str(value or "").strip()
    if "@" not in text:
        return False
    _, _, revision = text.rpartition("@")
    return bool(revision) and revision.casefold() not in {
        "default",
        "latest",
        "stable",
    }


def _validate_positive_roi_analysis(
    analysis: Mapping[str, Any],
    *,
    operating_cost_usd: Any,
    causal_analysis: Mapping[str, Any],
    causal_analysis_sha256: Any,
    context: _EvidenceContext,
) -> bool:
    try:
        if (
            analysis.get("schema_version") != ROI_ANALYSIS_SCHEMA_VERSION
            or _normalized_comparison(analysis.get("comparison")) != "b_vs_c"
            or _normalized_sha256(
                analysis.get("causal_analysis_sha256")
            )
            != _normalized_sha256(causal_analysis_sha256)
        ):
            return False
        lineage = _required_mapping(
            analysis.get("lineage"),
            field="ROI lineage",
        )
        business_value_descriptor = _required_mapping(
            lineage.get("business_value_protocol"),
            field="ROI business value protocol",
        )
        cost_descriptor = _required_mapping(
            lineage.get("cost_provenance"),
            field="ROI cost provenance",
        )
        if not (
            _is_hashed_artifact(business_value_descriptor)
            and _is_hashed_artifact(cost_descriptor)
        ):
            return False
        business_value_protocol = context.resolve_json(
            business_value_descriptor.get("ref"),
            business_value_descriptor.get("sha256"),
        )
        cost_provenance = context.resolve_json(
            cost_descriptor.get("ref"),
            cost_descriptor.get("sha256"),
        )
        if not (
            isinstance(business_value_protocol, Mapping)
            and isinstance(cost_provenance, Mapping)
        ):
            return False

        measurement = _required_mapping(
            analysis.get("measurement"),
            field="ROI measurement",
        )
        result = _required_mapping(
            analysis.get("result"),
            field="ROI result",
        )
        causal_result = _required_mapping(
            causal_analysis.get("result"),
            field="causal result",
        )
        experiment_id = _required_text(
            causal_analysis.get("experiment_id"),
            field="causal experiment_id",
        )
        causal_task_count = _required_positive_int(
            causal_result.get("task_count"),
            field="causal task_count",
        )
        first_execution_started_at_ms = (
            _causal_first_execution_started_at_ms(
                causal_analysis,
                context=context,
            )
        )
        value_per_success = _parse_business_value_protocol(
            business_value_protocol,
            expected_experiment_id=experiment_id,
            expected_task_count=causal_task_count,
            first_execution_started_at_ms=first_execution_started_at_ms,
        )
        cost_summary = _parse_incremental_cost_provenance(
            cost_provenance,
            expected_experiment_id=experiment_id,
            expected_causal_analysis_sha256=causal_analysis_sha256,
            expected_business_value_protocol_sha256=(
                business_value_descriptor.get("sha256")
            ),
            expected_task_count=causal_task_count,
            first_execution_started_at_ms=first_execution_started_at_ms,
        )
        task_count = _required_positive_int(
            measurement.get("task_count"),
            field="ROI task_count",
        )
        baseline_successes = _required_nonnegative_int(
            measurement.get("baseline_successes"),
            field="ROI baseline_successes",
        )
        supervisor_successes = _required_nonnegative_int(
            measurement.get("supervisor_successes"),
            field="ROI supervisor_successes",
        )
        baseline_cost = _required_nonnegative_number(
            measurement.get("baseline_cost_usd"),
            field="ROI baseline_cost_usd",
        )
        supervisor_cost = _required_nonnegative_number(
            measurement.get("supervisor_cost_usd"),
            field="ROI supervisor_cost_usd",
        )
        declared_value_per_success = _required_nonnegative_number(
            measurement.get("value_per_success_usd"),
            field="ROI value_per_success_usd",
        )
        measured_operating_cost = _required_nonnegative_number(
            operating_cost_usd,
            field="operating cost_usd",
        )
        n11 = _required_nonnegative_int(
            causal_result.get("n11"),
            field="causal n11",
        )
        n10 = _required_nonnegative_int(
            causal_result.get("n10"),
            field="causal n10",
        )
        n01 = _required_nonnegative_int(
            causal_result.get("n01"),
            field="causal n01",
        )
        if not (
            task_count == causal_task_count
            and baseline_successes == n11 + n01
            and supervisor_successes == n11 + n10
            and baseline_successes <= task_count
            and supervisor_successes <= task_count
            and _numbers_close(
                baseline_cost,
                cost_summary["baseline_cost_usd"],
            )
            and _numbers_close(
                supervisor_cost,
                cost_summary["supervisor_cost_usd"],
            )
            and _numbers_close(
                declared_value_per_success,
                value_per_success,
            )
            and math.isclose(
                measured_operating_cost,
                supervisor_cost,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            return False

        incremental_successes = supervisor_successes - baseline_successes
        incremental_cost = supervisor_cost - baseline_cost
        if incremental_successes <= 0:
            return False
        cost_per_incremental_success = (
            incremental_cost / incremental_successes
        )
        incremental_value = incremental_successes * value_per_success
        net_value = incremental_value - incremental_cost
        declared_incremental_successes = _required_positive_int(
            result.get("incremental_successes"),
            field="ROI incremental_successes",
        )
        declared_incremental_cost = _required_number(
            result.get("incremental_cost_usd"),
            field="ROI incremental_cost_usd",
        )
        declared_cost_per_success = _required_number(
            result.get("cost_per_incremental_success_usd"),
            field="ROI cost_per_incremental_success_usd",
        )
        declared_break_even = _required_number(
            result.get("break_even_value_per_success_usd"),
            field="ROI break_even_value_per_success_usd",
        )
        declared_incremental_value = _required_nonnegative_number(
            result.get("incremental_value_usd"),
            field="ROI incremental_value_usd",
        )
        declared_net_value = _required_number(
            result.get("net_value_usd"),
            field="ROI net_value_usd",
        )
        return (
            result.get("positive_roi") is True
            and declared_incremental_successes == incremental_successes
            and _numbers_close(
                declared_incremental_cost,
                incremental_cost,
            )
            and _numbers_close(
                declared_cost_per_success,
                cost_per_incremental_success,
            )
            and _numbers_close(
                declared_break_even,
                cost_per_incremental_success,
            )
            and _numbers_close(
                declared_incremental_value,
                incremental_value,
            )
            and _numbers_close(declared_net_value, net_value)
            and value_per_success > cost_per_incremental_success
            and net_value > 0.0
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _causal_first_execution_started_at_ms(
    causal_analysis: Mapping[str, Any],
    *,
    context: _EvidenceContext,
) -> int:
    experiment_id = _required_text(
        causal_analysis.get("experiment_id"),
        field="causal experiment_id",
    )
    design = _required_mapping(
        causal_analysis.get("design"),
        field="causal design",
    )
    lineage = _required_mapping(
        causal_analysis.get("lineage"),
        field="causal lineage",
    )
    descriptor = _required_mapping(
        lineage.get("assignments"),
        field="causal assignments",
    )
    if not _is_hashed_artifact(descriptor):
        raise ValueError("causal assignments must be a hashed artifact")
    document = context.resolve_json(
        descriptor.get("ref"),
        descriptor.get("sha256"),
    )
    if not isinstance(document, Mapping):
        raise ValueError("causal assignments could not be resolved")
    assignments = _parse_assignment_evidence(
        document,
        experiment_id=experiment_id,
        powered_design=design,
    )
    return min(
        assignment.first_execution_started_at_ms
        for assignment in assignments.values()
    )


def _parse_business_value_protocol(
    protocol: Mapping[str, Any],
    *,
    expected_experiment_id: str,
    expected_task_count: int,
    first_execution_started_at_ms: int,
) -> float:
    if (
        protocol.get("schema_version")
        != BUSINESS_VALUE_PROTOCOL_SCHEMA_VERSION
        or protocol.get("frozen") is not True
        or _normalized_token(protocol.get("metric"))
        != "independent_hidden_verifier_pass"
        or str(protocol.get("currency") or "").strip().upper() != "USD"
        or _normalized_token(protocol.get("decision_rule"))
        != "net_value_gt_zero"
        or _required_text(
            protocol.get("experiment_id"),
            field="business value experiment_id",
        )
        != expected_experiment_id
        or _required_positive_int(
            protocol.get("decision_horizon_task_count"),
            field="business value decision horizon",
        )
        != expected_task_count
    ):
        raise ValueError("business value protocol is invalid")
    _required_text(
        protocol.get("protocol_id"),
        field="business value protocol_id",
    )
    registered_at_ms = _required_positive_int(
        protocol.get("registered_at_ms"),
        field="business value registered_at_ms",
    )
    if registered_at_ms >= first_execution_started_at_ms:
        raise ValueError("business value was not preregistered")
    valuation_basis = _required_mapping(
        protocol.get("valuation_basis"),
        field="business value valuation_basis",
    )
    if (
        _normalized_token(valuation_basis.get("kind"))
        != "approved_business_case"
        or not _required_text(
            valuation_basis.get("owner"),
            field="business value owner",
        )
        or not _required_text(
            valuation_basis.get("reference"),
            field="business value reference",
        )
    ):
        raise ValueError("business value basis is incomplete")
    value_per_success = _required_nonnegative_number(
        protocol.get("value_per_success_usd"),
        field="business value per success",
    )
    if value_per_success <= 0.0:
        raise ValueError("business value per success must be positive")
    return value_per_success


def _parse_incremental_cost_provenance(
    provenance: Mapping[str, Any],
    *,
    expected_experiment_id: str,
    expected_causal_analysis_sha256: Any,
    expected_business_value_protocol_sha256: Any,
    expected_task_count: int,
    first_execution_started_at_ms: int,
) -> dict[str, float]:
    if (
        provenance.get("schema_version")
        != INCREMENTAL_COST_PROVENANCE_SCHEMA_VERSION
        or _required_text(
            provenance.get("experiment_id"),
            field="cost experiment_id",
        )
        != expected_experiment_id
        or _normalized_sha256(
            provenance.get("causal_analysis_sha256")
        )
        != _normalized_sha256(expected_causal_analysis_sha256)
        or _normalized_sha256(
            provenance.get("business_value_protocol_sha256")
        )
        != _normalized_sha256(
            expected_business_value_protocol_sha256
        )
        or _required_positive_int(
            provenance.get("task_count"),
            field="cost task_count",
        )
        != expected_task_count
    ):
        raise ValueError("incremental cost provenance is not bound")
    _required_text(
        provenance.get("measurement_id"),
        field="cost measurement_id",
    )
    started_at_ms = _required_positive_int(
        provenance.get("measurement_started_at_ms"),
        field="cost measurement_started_at_ms",
    )
    completed_at_ms = _required_positive_int(
        provenance.get("measurement_completed_at_ms"),
        field="cost measurement_completed_at_ms",
    )
    if not (
        first_execution_started_at_ms <= started_at_ms < completed_at_ms
    ):
        raise ValueError("incremental cost window is invalid")
    components = _required_mapping(
        provenance.get("components"),
        field="cost components",
    )
    if set(components) != {"compute", "latency", "risk"}:
        raise ValueError(
            "cost provenance must include compute, latency, and risk"
        )
    arm_totals = {"baseline": 0.0, "supervisor": 0.0}
    for component_name in ("compute", "latency", "risk"):
        component = _required_mapping(
            components.get(component_name),
            field=f"{component_name} cost",
        )
        for arm in arm_totals:
            arm_record = _required_mapping(
                component.get(arm),
                field=f"{component_name} {arm} cost",
            )
            arm_totals[arm] += _recomputed_component_cost(
                component_name,
                method=component.get("method"),
                record=arm_record,
            )
    totals = _required_mapping(
        provenance.get("totals"),
        field="cost totals",
    )
    declared_baseline = _required_nonnegative_number(
        totals.get("baseline_cost_usd"),
        field="baseline total cost",
    )
    declared_supervisor = _required_nonnegative_number(
        totals.get("supervisor_cost_usd"),
        field="supervisor total cost",
    )
    declared_incremental = _required_number(
        totals.get("incremental_cost_usd"),
        field="incremental total cost",
    )
    if not (
        _numbers_close(declared_baseline, arm_totals["baseline"])
        and _numbers_close(
            declared_supervisor,
            arm_totals["supervisor"],
        )
        and _numbers_close(
            declared_incremental,
            declared_supervisor - declared_baseline,
        )
    ):
        raise ValueError("cost totals do not match component provenance")
    return {
        "baseline_cost_usd": declared_baseline,
        "supervisor_cost_usd": declared_supervisor,
    }


def _recomputed_component_cost(
    component_name: str,
    *,
    method: Any,
    record: Mapping[str, Any],
) -> float:
    reported_cost = _required_nonnegative_number(
        record.get("reported_cost_usd"),
        field=f"{component_name} reported cost",
    )
    if component_name == "compute":
        if _normalized_token(method) != "token_usage_pricing":
            raise ValueError("compute cost method is invalid")
        input_tokens = _required_nonnegative_int(
            record.get("input_tokens"),
            field="compute input_tokens",
        )
        output_tokens = _required_nonnegative_int(
            record.get("output_tokens"),
            field="compute output_tokens",
        )
        input_rate = _required_nonnegative_number(
            record.get("input_usd_per_million"),
            field="compute input rate",
        )
        output_rate = _required_nonnegative_number(
            record.get("output_usd_per_million"),
            field="compute output rate",
        )
        recomputed = (
            input_tokens * input_rate
            + output_tokens * output_rate
        ) / 1_000_000
    elif component_name == "latency":
        if _normalized_token(method) != "elapsed_seconds_value":
            raise ValueError("latency cost method is invalid")
        elapsed_seconds = _required_nonnegative_number(
            record.get("elapsed_seconds"),
            field="latency elapsed_seconds",
        )
        usd_per_hour = _required_nonnegative_number(
            record.get("usd_per_hour"),
            field="latency usd_per_hour",
        )
        recomputed = elapsed_seconds * usd_per_hour / 3_600
    elif component_name == "risk":
        if _normalized_token(method) != "expected_loss":
            raise ValueError("risk cost method is invalid")
        probability = _required_number(
            record.get("probability"),
            field="risk probability",
        )
        impact = _required_nonnegative_number(
            record.get("impact_usd"),
            field="risk impact",
        )
        if not 0.0 <= probability <= 1.0:
            raise ValueError("risk probability must be within [0, 1]")
        recomputed = probability * impact
    else:
        raise ValueError("unknown cost component")
    if not _numbers_close(reported_cost, recomputed):
        raise ValueError(
            f"{component_name} reported cost does not recompute"
        )
    return recomputed


def _validate_auto_improvement_authority_manifest(
    manifest: Mapping[str, Any],
    *,
    descriptors: Mapping[str, Mapping[str, Any]],
    receipts: Mapping[str, Mapping[str, Any]],
) -> bool:
    try:
        if (
            manifest.get("schema_version")
            != AUTO_IMPROVEMENT_AUTHORITY_MANIFEST_SCHEMA_VERSION
        ):
            return False
        change_id = _required_text(
            manifest.get("change_id"),
            field="auto-improvement authority change_id",
        )
        receipt_hashes = _required_mapping(
            manifest.get("receipt_hashes"),
            field="auto-improvement authority receipt_hashes",
        )
        if set(receipt_hashes) != set(descriptors):
            return False
        return (
            set(receipts) == set(descriptors)
            and all(
                _required_text(
                    receipt.get("change_id"),
                    field=f"{name} authority change_id",
                )
                == change_id
                for name, receipt in receipts.items()
            )
            and all(
                _normalized_sha256(receipt_hashes.get(name))
                == _normalized_sha256(descriptor.get("sha256"))
                for name, descriptor in descriptors.items()
            )
        )
    except (TypeError, ValueError):
        return False


def _validate_auto_improvement_receipts(
    receipts: Mapping[str, Mapping[str, Any]],
    *,
    descriptors: Mapping[str, Mapping[str, Any]],
) -> bool:
    try:
        frozen_control = receipts["frozen_control"]
        sealed_holdout = receipts["sealed_holdout"]
        shadow_result = receipts["shadow_result"]
        human_approval = receipts["human_approval"]
        canary = receipts["canary"]
        rollback = receipts["rollback_receipt"]

        if (
            frozen_control.get("schema_version")
            != FROZEN_CONTROL_RECEIPT_SCHEMA_VERSION
            or frozen_control.get("frozen") is not True
        ):
            return False
        change_id = _required_text(
            frozen_control.get("change_id"),
            field="auto-improvement change_id",
        )
        control_policy_sha256 = _required_sha256(
            frozen_control.get("control_policy_sha256"),
            field="frozen control policy",
        )
        candidate_policy_sha256 = _required_sha256(
            frozen_control.get("candidate_policy_sha256"),
            field="candidate policy",
        )
        if control_policy_sha256 == candidate_policy_sha256:
            return False
        frozen_at_ms = _required_positive_int(
            frozen_control.get("frozen_at_ms"),
            field="control frozen_at_ms",
        )

        if (
            sealed_holdout.get("schema_version")
            != SEALED_HOLDOUT_RECEIPT_SCHEMA_VERSION
            or sealed_holdout.get("sealed") is not True
            or _required_text(
                sealed_holdout.get("change_id"),
                field="holdout change_id",
            )
            != change_id
        ):
            return False
        holdout_dataset_sha256 = _required_sha256(
            sealed_holdout.get("dataset_sha256"),
            field="holdout dataset",
        )
        _required_sha256(
            sealed_holdout.get("access_log_sha256"),
            field="holdout access log",
        )
        sealed_at_ms = _required_positive_int(
            sealed_holdout.get("sealed_at_ms"),
            field="holdout sealed_at_ms",
        )
        opened_at_ms = _required_positive_int(
            sealed_holdout.get("opened_at_ms"),
            field="holdout opened_at_ms",
        )
        holdout_completed_at_ms = _required_positive_int(
            sealed_holdout.get("evaluation_completed_at_ms"),
            field="holdout evaluation_completed_at_ms",
        )
        if not (
            frozen_at_ms < sealed_at_ms < opened_at_ms
            <= holdout_completed_at_ms
        ):
            return False

        if (
            shadow_result.get("schema_version")
            != SHADOW_RESULT_SCHEMA_VERSION
            or shadow_result.get("passed") is not True
            or _required_text(
                shadow_result.get("change_id"),
                field="shadow change_id",
            )
            != change_id
            or _required_sha256(
                shadow_result.get("control_policy_sha256"),
                field="shadow control policy",
            )
            != control_policy_sha256
            or _required_sha256(
                shadow_result.get("candidate_policy_sha256"),
                field="shadow candidate policy",
            )
            != candidate_policy_sha256
            or _required_sha256(
                shadow_result.get("holdout_dataset_sha256"),
                field="shadow holdout dataset",
            )
            != holdout_dataset_sha256
            or _required_sha256(
                shadow_result.get("frozen_control_receipt_sha256"),
                field="shadow frozen control receipt",
            )
            != _descriptor_sha256(descriptors, "frozen_control")
            or _required_sha256(
                shadow_result.get("sealed_holdout_receipt_sha256"),
                field="shadow sealed holdout receipt",
            )
            != _descriptor_sha256(descriptors, "sealed_holdout")
        ):
            return False
        shadow_started_at_ms = _required_positive_int(
            shadow_result.get("started_at_ms"),
            field="shadow started_at_ms",
        )
        shadow_completed_at_ms = _required_positive_int(
            shadow_result.get("completed_at_ms"),
            field="shadow completed_at_ms",
        )
        shadow_task_count = _required_positive_int(
            shadow_result.get("task_count"),
            field="shadow task_count",
        )
        shadow_control_successes = _required_nonnegative_int(
            shadow_result.get("control_successes"),
            field="shadow control successes",
        )
        shadow_candidate_successes = _required_nonnegative_int(
            shadow_result.get("candidate_successes"),
            field="shadow candidate successes",
        )
        if not (
            sealed_at_ms < shadow_started_at_ms <= opened_at_ms
            and opened_at_ms <= shadow_completed_at_ms
            == holdout_completed_at_ms
            and shadow_control_successes <= shadow_task_count
            and shadow_candidate_successes <= shadow_task_count
            and shadow_candidate_successes >= shadow_control_successes
            and _required_nonnegative_int(
                shadow_result.get("guardrail_regressions"),
                field="shadow guardrail regressions",
            )
            == 0
        ):
            return False

        if (
            rollback.get("schema_version")
            != ROLLBACK_RECEIPT_SCHEMA_VERSION
            or rollback.get("passed") is not True
            or _required_text(
                rollback.get("change_id"),
                field="rollback change_id",
            )
            != change_id
            or _required_sha256(
                rollback.get("candidate_policy_sha256"),
                field="rollback candidate policy",
            )
            != candidate_policy_sha256
            or _required_sha256(
                rollback.get("restored_control_policy_sha256"),
                field="rollback restored control",
            )
            != control_policy_sha256
            or _required_sha256(
                rollback.get("frozen_control_receipt_sha256"),
                field="rollback frozen control receipt",
            )
            != _descriptor_sha256(descriptors, "frozen_control")
            or _normalized_token(rollback.get("exercise"))
            != "restore_frozen_control"
        ):
            return False
        _required_sha256(
            rollback.get("rollback_plan_sha256"),
            field="rollback plan",
        )
        rollback_tested_at_ms = _required_positive_int(
            rollback.get("tested_at_ms"),
            field="rollback tested_at_ms",
        )
        restore_seconds = _required_number(
            rollback.get("restore_seconds"),
            field="rollback restore_seconds",
        )
        if (
            rollback_tested_at_ms <= shadow_completed_at_ms
            or restore_seconds <= 0.0
        ):
            return False

        if (
            human_approval.get("schema_version")
            != HUMAN_APPROVAL_RECEIPT_SCHEMA_VERSION
            or human_approval.get("approved") is not True
            or _normalized_token(human_approval.get("approver_type"))
            != "human"
            or _normalized_token(human_approval.get("decision"))
            != "approved"
            or _required_text(
                human_approval.get("change_id"),
                field="approval change_id",
            )
            != change_id
            or _required_sha256(
                human_approval.get("shadow_result_sha256"),
                field="approval shadow result",
            )
            != _descriptor_sha256(descriptors, "shadow_result")
            or _required_sha256(
                human_approval.get("sealed_holdout_receipt_sha256"),
                field="approval sealed holdout receipt",
            )
            != _descriptor_sha256(descriptors, "sealed_holdout")
            or _required_sha256(
                human_approval.get("rollback_receipt_sha256"),
                field="approval rollback receipt",
            )
            != _descriptor_sha256(descriptors, "rollback_receipt")
        ):
            return False
        approver = _required_mapping(
            human_approval.get("approver"),
            field="named human approver",
        )
        for field_name in ("name", "identity", "role"):
            _required_text(
                approver.get(field_name),
                field=f"approver {field_name}",
            )
        approved_at_ms = _required_positive_int(
            human_approval.get("approved_at_ms"),
            field="approval approved_at_ms",
        )
        if approved_at_ms <= max(
            shadow_completed_at_ms,
            rollback_tested_at_ms,
        ):
            return False

        if (
            canary.get("schema_version") != CANARY_RESULT_SCHEMA_VERSION
            or canary.get("passed") is not True
            or _required_text(
                canary.get("change_id"),
                field="canary change_id",
            )
            != change_id
            or _required_sha256(
                canary.get("candidate_policy_sha256"),
                field="canary candidate policy",
            )
            != candidate_policy_sha256
            or _required_sha256(
                canary.get("shadow_result_sha256"),
                field="canary shadow result",
            )
            != _descriptor_sha256(descriptors, "shadow_result")
            or _required_sha256(
                canary.get("human_approval_receipt_sha256"),
                field="canary human approval",
            )
            != _descriptor_sha256(descriptors, "human_approval")
            or _required_sha256(
                canary.get("rollback_receipt_sha256"),
                field="canary rollback receipt",
            )
            != _descriptor_sha256(descriptors, "rollback_receipt")
        ):
            return False
        deployed_at_ms = _required_positive_int(
            canary.get("deployed_at_ms"),
            field="canary deployed_at_ms",
        )
        completed_at_ms = _required_positive_int(
            canary.get("completed_at_ms"),
            field="canary completed_at_ms",
        )
        traffic_fraction = _required_number(
            canary.get("traffic_fraction"),
            field="canary traffic_fraction",
        )
        sample_size = _required_positive_int(
            canary.get("sample_size"),
            field="canary sample_size",
        )
        control_successes = _required_nonnegative_int(
            canary.get("control_successes"),
            field="canary control successes",
        )
        candidate_successes = _required_nonnegative_int(
            canary.get("candidate_successes"),
            field="canary candidate successes",
        )
        return (
            approved_at_ms < deployed_at_ms < completed_at_ms
            and 0.0 < traffic_fraction < 1.0
            and control_successes <= sample_size
            and candidate_successes <= sample_size
            and candidate_successes >= control_successes
            and _required_nonnegative_int(
                canary.get("guardrail_regressions"),
                field="canary guardrail regressions",
            )
            == 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def _descriptor_sha256(
    descriptors: Mapping[str, Mapping[str, Any]],
    name: str,
) -> str:
    try:
        descriptor = descriptors[name]
    except KeyError as exc:
        raise ValueError(f"missing {name} descriptor") from exc
    return _required_sha256(
        descriptor.get("sha256"),
        field=f"{name} descriptor sha256",
    )


def _required_sha256(value: Any, *, field: str) -> str:
    normalized = _normalized_sha256(value)
    if normalized is None:
        raise ValueError(f"{field} must be SHA-256")
    return normalized


def _parse_assignment_evidence(
    document: Mapping[str, Any],
    *,
    experiment_id: str,
    powered_design: Mapping[str, Any],
) -> dict[str, _AssignmentEvidence]:
    if (
        document.get("schema_version")
        != B_VS_C_ASSIGNMENTS_SCHEMA_VERSION
    ):
        raise ValueError("unsupported assignment evidence schema")
    if _required_text(
        document.get("experiment_id"),
        field="assignment experiment_id",
    ) != experiment_id:
        raise ValueError("assignment experiment_id mismatch")
    assignment_version = _required_text(
        document.get("assignment_version"),
        field="assignment_version",
    )
    if assignment_version != _required_text(
        powered_design.get("assignment_version"),
        field="powered design assignment_version",
    ):
        raise ValueError(
            "assignment version does not match preregistered design"
        )
    if _normalized_sha256(
        document.get("powered_design_sha256")
    ) != _sha256_canonical_json(powered_design):
        raise ValueError("powered design is not pinned by assignments")
    design_key_commitment = _required_sha256(
        powered_design.get("assignment_key_commitment_sha256"),
        field="powered design assignment key commitment",
    )
    design_treatments = _required_mapping(
        powered_design.get("treatment_hashes"),
        field="powered design treatment_hashes",
    )
    expected_treatment_hashes = {
        arm: _required_sha256(
            design_treatments.get(arm),
            field=f"powered design treatment hash {arm}",
        )
        for arm in _POWERED_DESIGN_ARM_KEYS
    }
    if (
        len(design_treatments) != len(expected_treatment_hashes)
        or len(set(expected_treatment_hashes.values()))
        != len(expected_treatment_hashes)
    ):
        raise ValueError(
            "powered design must bind one distinct treatment hash per arm"
        )
    expected_experiment_spec_hash = _required_sha256(
        powered_design.get("experiment_spec_hash"),
        field="powered design experiment_spec_hash",
    )
    expected_task_strata_manifest_sha256 = _required_sha256(
        powered_design.get("task_strata_manifest_sha256"),
        field="powered design task_strata_manifest_sha256",
    )
    randomization = _required_mapping(
        document.get("randomization"),
        field="assignment randomization",
    )
    if _normalized_token(randomization.get("method")) != "hmac_sha256":
        raise ValueError("assignment randomization must use HMAC-SHA256")
    if _normalized_token(
        randomization.get("assignment_unit")
    ) != "task":
        raise ValueError("assignment unit must be task")
    if (
        _normalized_sha256(
            randomization.get("key_commitment_sha256")
        )
        != design_key_commitment
    ):
        raise ValueError(
            "assignment key commitment does not match preregistered design"
        )
    raw_hmac_key = _required_text(
        randomization.get("hmac_key_hex"),
        field="assignment HMAC key reveal",
    )
    try:
        hmac_key = bytes.fromhex(raw_hmac_key)
    except ValueError as exc:
        raise ValueError("assignment HMAC key reveal is invalid") from exc
    if (
        not hmac_key
        or sha256(hmac_key).hexdigest()
        != design_key_commitment
    ):
        raise ValueError("assignment HMAC key does not match commitment")

    assignment_roster: tuple[str, ...] | None = None
    raw_roster = document.get("assignment_roster")
    if raw_roster is not None:
        roster_values = _required_sequence(
            raw_roster,
            field="assignment_roster",
        )
        assignment_roster = tuple(
            _required_text(value, field="assignment_roster item")
            for value in roster_values
        )
        if (
            not assignment_roster
            or len(assignment_roster) != len(set(assignment_roster))
        ):
            raise ValueError(
                "assignment_roster must contain unique identities"
            )

    records = _required_sequence(
        document.get("assignments"),
        field="assignments",
    )
    assignments: dict[str, _AssignmentEvidence] = {}
    assignment_methods: set[str] = set()
    stratum_positions: set[int] = set()
    task_strata: list[dict[str, Any]] = []
    for raw_record in records:
        record = _required_mapping(raw_record, field="assignment")
        task_id = _required_text(
            record.get("task_id"),
            field="assignment.task_id",
        )
        if task_id in assignments:
            raise ValueError("duplicate assignment task")
        task_identity = _required_mapping(
            record.get("task_identity"),
            field="assignment.task_identity",
        )
        canonical_task_id = canonical_task_identity(task_identity)
        if (
            _normalized_sha256(record.get("canonical_task_id"))
            != canonical_task_id
            or any(
                existing.canonical_task_id == canonical_task_id
                for existing in assignments.values()
            )
        ):
            raise ValueError(
                "assignment canonical task identity is missing or aliased"
            )
        assignment_id = _normalized_sha256(record.get("assignment_id"))
        if assignment_id is None:
            raise ValueError("assignment_id must be a sha256 HMAC output")
        if _required_text(
            record.get("experiment_id"),
            field="assignment.experiment_id",
        ) != experiment_id:
            raise ValueError("assignment record experiment_id mismatch")
        if _required_text(
            record.get("assignment_version"),
            field="assignment.assignment_version",
        ) != assignment_version:
            raise ValueError("assignment record version mismatch")
        (
            expected_block,
            expected_digest,
            expected_order,
        ) = _expected_kernel_assignment(
            record,
            experiment_id=experiment_id,
            assignment_version=assignment_version,
            hmac_key=hmac_key,
            task_identity=task_identity,
            assignment_roster=assignment_roster,
            expected_treatment_hashes=expected_treatment_hashes,
            expected_experiment_spec_hash=(
                expected_experiment_spec_hash
            ),
        )
        block = _required_mapping(
            record.get("block"),
            field="assignment.block",
        )
        assignment_method = _required_text(
            block.get("assignment_method"),
            field="assignment.block.assignment_method",
        )
        assignment_methods.add(assignment_method)
        stratum_positions.add(
            int(
                _required_text(
                    block.get("stratum_position"),
                    field="assignment.block.stratum_position",
                )
            )
        )
        if dict(block) != expected_block:
            raise ValueError(
                "assignment block does not match kernel derivation"
            )
        task_strata.append(
            {
                "task_id": task_id,
                "canonical_task_id": canonical_task_id,
                "stratum": _assignment_stratum_from_block(block),
            }
        )
        if not hmac.compare_digest(assignment_id, expected_digest):
            raise ValueError(
                "assignment id does not match deterministic HMAC"
            )
        order_values = _required_sequence(
            record.get("order"),
            field="assignment.order",
        )
        order = tuple(
            _required_text(value, field="assignment.order item")
            for value in order_values
        )
        if order != expected_order:
            raise ValueError(
                "assignment order does not match kernel derivation"
            )
        assigned_at_ms = _required_positive_int(
            record.get("assigned_at_ms"),
            field="assignment.assigned_at_ms",
        )
        persisted_at_ms = _required_positive_int(
            record.get("persisted_at_ms"),
            field="assignment.persisted_at_ms",
        )
        first_execution_started_at_ms = _required_positive_int(
            record.get("first_execution_started_at_ms"),
            field="assignment.first_execution_started_at_ms",
        )
        if not (
            assigned_at_ms
            <= persisted_at_ms
            < first_execution_started_at_ms
        ):
            raise ValueError(
                "assignment must be persisted before arm execution"
            )
        assignments[task_id] = _AssignmentEvidence(
            task_id=task_id,
            canonical_task_id=canonical_task_id,
            assignment_id=assignment_id,
            first_execution_started_at_ms=first_execution_started_at_ms,
        )
    if not assignments:
        raise ValueError("assignment evidence is empty")
    if len(assignment_methods) != 1:
        raise ValueError("assignment methods must be consistent")
    frozen_roster = assignment_methods == {
        _FROZEN_ROSTER_ASSIGNMENT_METHOD
    }
    if frozen_roster:
        if (
            assignment_roster is None
            or len(assignment_roster) != len(assignments)
            or stratum_positions != set(range(len(assignment_roster)))
        ):
            raise ValueError(
                "frozen-roster assignments must cover one contiguous "
                "position per roster identity"
            )
    elif assignment_roster is not None:
        raise ValueError(
            "assignment_roster is only valid for frozen-roster assignments"
        )
    if (
        _task_strata_manifest_sha256(task_strata)
        != expected_task_strata_manifest_sha256
    ):
        raise ValueError(
            "task-to-stratum manifest does not match preregistered design"
        )
    return assignments


def _assignment_stratum_from_block(
    block: Mapping[str, Any],
) -> dict[str, str]:
    stratum = {
        key: value
        for key, value in block.items()
        if key not in _ASSIGNMENT_DERIVED_BLOCK_KEYS
    }
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in stratum.items()
    ):
        raise ValueError("assignment stratum must map text to text")
    return dict(stratum)


def _task_strata_manifest_sha256(
    task_strata: Sequence[Mapping[str, Any]],
) -> str:
    normalized: list[dict[str, Any]] = []
    for raw_entry in task_strata:
        entry = _required_mapping(
            raw_entry,
            field="task-to-stratum entry",
        )
        stratum = _required_mapping(
            entry.get("stratum"),
            field="task-to-stratum stratum",
        )
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in stratum.items()
        ):
            raise ValueError(
                "task-to-stratum manifest must map text to text"
            )
        normalized.append(
            {
                "task_id": _required_text(
                    entry.get("task_id"),
                    field="task-to-stratum task_id",
                ),
                "canonical_task_id": _required_sha256(
                    entry.get("canonical_task_id"),
                    field="task-to-stratum canonical_task_id",
                ),
                "stratum": dict(stratum),
            }
        )
    normalized.sort(
        key=lambda item: (
            item["canonical_task_id"],
            item["task_id"],
        )
    )
    if (
        not normalized
        or len({
            (item["task_id"], item["canonical_task_id"])
            for item in normalized
        })
        != len(normalized)
    ):
        raise ValueError(
            "task-to-stratum manifest must contain unique tasks"
        )
    return _sha256_canonical_json(
        {
            "schema_version": TASK_STRATA_MANIFEST_SCHEMA_VERSION,
            "tasks": normalized,
        }
    )


def _expected_kernel_assignment(
    record: Mapping[str, Any],
    *,
    experiment_id: str,
    assignment_version: str,
    hmac_key: bytes,
    task_identity: Mapping[str, Any],
    assignment_roster: tuple[str, ...] | None,
    expected_treatment_hashes: Mapping[str, str],
    expected_experiment_spec_hash: str,
) -> tuple[dict[str, str], str, tuple[str, ...]]:
    from . import experiment_kernel

    block = _required_mapping(record.get("block"), field="assignment.block")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in block.items()
    ):
        raise ValueError("assignment block must map strings to strings")
    stratum = _assignment_stratum_from_block(block)
    if stratum.get("stratum_id") != experiment_kernel._sha256_json({
        key: value
        for key, value in stratum.items()
        if key != "stratum_id"
    }):
        raise ValueError("assignment stratum identity mismatch")
    if (
        _normalized_sha256(block.get("experiment_spec_hash"))
        != expected_experiment_spec_hash
    ):
        raise ValueError(
            "experiment spec hash does not match preregistered design"
        )
    raw_position = block.get("stratum_position")
    if not isinstance(raw_position, str) or not re.fullmatch(
        r"0|[1-9][0-9]*",
        raw_position,
    ):
        raise ValueError("assignment stratum position is invalid")
    stratum_position = int(raw_position)
    raw_treatments = _required_mapping(
        record.get("treatment_hashes"),
        field="assignment.treatment_hashes",
    )
    treatment_hashes: dict[Any, str] = {}
    for arm in experiment_kernel.Arm:
        digest = _normalized_sha256(raw_treatments.get(arm.value))
        if (
            digest is None
            or digest != expected_treatment_hashes.get(arm.value)
        ):
            raise ValueError(
                "assignment treatment hashes do not match powered design"
            )
        treatment_hashes[arm] = digest
    if len(raw_treatments) != len(treatment_hashes) or len(
        set(treatment_hashes.values())
    ) != len(treatment_hashes):
        raise ValueError(
            "assignment must bind one distinct treatment hash per arm"
        )
    frozen_roster = (
        block.get("assignment_method") == _FROZEN_ROSTER_ASSIGNMENT_METHOD
    )
    if frozen_roster != (assignment_roster is not None):
        raise ValueError(
            "assignment method does not match assignment_roster"
        )
    roster_hash = stratum.get("assignment_roster_hash")
    if assignment_roster is None:
        if roster_hash is not None:
            raise ValueError(
                "non-frozen assignment cannot declare assignment roster hash"
            )
    elif roster_hash != experiment_kernel._sha256_json({
        "assignment_roster": list(assignment_roster),
    }):
        raise ValueError(
            "assignment roster hash does not match frozen roster"
        )
    experiment = SimpleNamespace(
        experiment_id=experiment_id,
        assignment_version=assignment_version,
        hmac_key=hmac_key,
        spec_hash=expected_experiment_spec_hash,
        treatment_hashes=treatment_hashes,
        metadata=(
            {"assignment_roster": assignment_roster}
            if assignment_roster is not None
            else {}
        ),
    )
    if assignment_roster is None:
        expected_stratum_position = (
            experiment_kernel._deterministic_stratum_position(
                experiment,
                task_identity,
                stratum=stratum,
            )
        )
    else:
        task_id = _required_text(
            record.get("task_id"),
            field="assignment.task_id",
        )
        canonical_task_key = _required_text(
            task_identity.get("canonical_task_key"),
            field="assignment.task_identity.canonical_task_key",
        )
        candidates = {
            canonical_task_identity(task_identity),
            canonical_task_key,
            task_id,
        }
        positions = [
            index
            for index, value in enumerate(assignment_roster)
            if value in candidates
        ]
        if len(positions) != 1:
            raise ValueError(
                "assignment task must appear exactly once in frozen roster"
            )
        expected_stratum_position = positions[0]
    if stratum_position != expected_stratum_position:
        raise ValueError(
            "assignment stratum position does not match deterministic rank"
        )
    (
        expected_block,
        expected_digest,
        expected_order,
    ) = experiment_kernel._derive_assignment(
        experiment,
        task_identity,
        stratum=stratum,
        stratum_position=stratum_position,
    )
    return (
        dict(expected_block),
        expected_digest,
        tuple(arm.value for arm in expected_order),
    )


def _validate_pilot_confirmation_lineage(
    document: Mapping[str, Any],
    *,
    experiment_id: str,
    design: Mapping[str, Any],
    assignments: Mapping[str, _AssignmentEvidence],
    confirmation_roster: Mapping[str, str],
    context: _EvidenceContext,
) -> bool:
    try:
        if (
            document.get("schema_version")
            != PILOT_CONFIRMATION_LINEAGE_SCHEMA_VERSION
        ):
            return False
        pilot = _required_mapping(document.get("pilot"), field="study.pilot")
        confirmation = _required_mapping(
            document.get("confirmation"),
            field="study.confirmation",
        )
        derivation = _required_mapping(
            document.get("derivation"),
            field="study.derivation",
        )
        if _normalized_token(derivation.get("kind")) != (
            "pilot_informs_confirmation"
        ):
            return False

        descriptors: dict[str, Mapping[str, Any]] = {}
        documents: dict[str, Mapping[str, Any]] = {}
        for phase, phase_payload, artifact_names in (
            (
                "pilot",
                pilot,
                (
                    "protocol",
                    "roster",
                    "assignments",
                    "terminal_outcomes",
                    "analysis",
                ),
            ),
            (
                "confirmation",
                confirmation,
                ("protocol", "roster"),
            ),
        ):
            for artifact_name in artifact_names:
                key = f"{phase}_{artifact_name}"
                descriptor = _required_mapping(
                    phase_payload.get(artifact_name),
                    field=f"study.{phase}.{artifact_name}",
                )
                if not _is_hashed_artifact(descriptor):
                    return False
                resolved = context.resolve_json(
                    descriptor.get("ref"),
                    descriptor.get("sha256"),
                )
                if not isinstance(resolved, Mapping):
                    return False
                descriptors[key] = descriptor
                documents[key] = resolved

        pilot_id = _required_text(
            pilot.get("experiment_id"),
            field="study pilot experiment_id",
        )
        confirmation_id = _required_text(
            confirmation.get("experiment_id"),
            field="study confirmation experiment_id",
        )
        if (
            pilot_id == confirmation_id
            or confirmation_id != experiment_id
        ):
            return False

        pilot_protocol = documents["pilot_protocol"]
        if not _valid_protocol_identity(
            pilot_protocol,
            phase="pilot",
            experiment_id=pilot_id,
        ):
            return False
        pilot_protocol_registered_at_ms = _required_positive_int(
            pilot_protocol.get("registered_at_ms"),
            field="pilot protocol registered_at_ms",
        )
        pilot_roster = documents["pilot_roster"]
        pilot_roster_tasks = _validated_roster_tasks(
            pilot_roster,
            phase="pilot",
            experiment_id=pilot_id,
            protocol_sha256=descriptors["pilot_protocol"].get("sha256"),
        )
        pilot_roster_frozen_at_ms = _required_positive_int(
            pilot_roster.get("frozen_at_ms"),
            field="pilot roster frozen_at_ms",
        )
        pilot_analysis = documents["pilot_analysis"]
        if (
            pilot_analysis.get("schema_version")
            != PILOT_ANALYSIS_SCHEMA_VERSION
            or _required_text(
                pilot_analysis.get("experiment_id"),
                field="pilot analysis experiment_id",
            )
            != pilot_id
            or _normalized_sha256(
                pilot_analysis.get("protocol_sha256")
            )
            != _normalized_sha256(
                descriptors["pilot_protocol"].get("sha256")
            )
            or _normalized_sha256(
                pilot_analysis.get("roster_sha256")
            )
            != _normalized_sha256(
                descriptors["pilot_roster"].get("sha256")
            )
            or _normalized_sha256(
                pilot_analysis.get("assignments_sha256")
            )
            != _normalized_sha256(
                descriptors["pilot_assignments"].get("sha256")
            )
            or _normalized_sha256(
                pilot_analysis.get("terminal_outcomes_sha256")
            )
            != _normalized_sha256(
                descriptors["pilot_terminal_outcomes"].get("sha256")
            )
        ):
            return False
        pilot_completed_at_ms = _required_positive_int(
            pilot_analysis.get("completed_at_ms"),
            field="pilot analysis completed_at_ms",
        )
        (
            pilot_b_win_rate,
            pilot_discordant_task_count,
        ) = _validated_pilot_estimates(
            analysis=pilot_analysis,
            assignments_document=documents["pilot_assignments"],
            assignments_sha256=descriptors["pilot_assignments"].get(
                "sha256"
            ),
            outcomes_document=documents["pilot_terminal_outcomes"],
            roster=pilot_roster_tasks,
            roster_sha256=descriptors["pilot_roster"].get("sha256"),
            experiment_id=pilot_id,
        )

        confirmation_protocol = documents["confirmation_protocol"]
        if not _valid_protocol_identity(
            confirmation_protocol,
            phase="confirmation",
            experiment_id=confirmation_id,
        ):
            return False
        confirmation_registered_at_ms = _required_positive_int(
            confirmation_protocol.get("registered_at_ms"),
            field="confirmation protocol registered_at_ms",
        )
        power = _required_mapping(design.get("power"), field="design.power")
        target_power = _required_number(
            power.get("target_power"),
            field="design target power",
        )
        preregistered_b_win_rate = _required_number(
            power.get("alternative_b_win_rate"),
            field="design alternative B win rate",
        )
        pilot_b_win_rate_ceiling = _wilson_interval(
            round(pilot_b_win_rate * pilot_discordant_task_count),
            pilot_discordant_task_count,
            _normal_quantile(0.975),
        )[1]
        if preregistered_b_win_rate > pilot_b_win_rate_ceiling:
            raise ClaimGateError(
                "preregistered alternative_b_win_rate "
                f"{preregistered_b_win_rate} is more optimistic than the "
                f"pilot estimate {pilot_b_win_rate} allows "
                f"(wilson-upper-95 bound {pilot_b_win_rate_ceiling})"
            )
        confirmation_plan = derive_confirmation_plan(
            PilotEstimate(
                task_count=len(pilot_roster_tasks),
                discordant_task_count=pilot_discordant_task_count,
                verifier_flake_count=0,
                infrastructure_failure_count=0,
                mean_cost_by_arm={"A": 0.0, "B": 0.0, "C": 0.0},
                mean_latency_ms_by_arm={"A": 0.0, "B": 0.0, "C": 0.0},
                mean_risk_cost_by_arm={"A": 0.0, "B": 0.0, "C": 0.0},
                task_ids=tuple(pilot_roster_tasks),
                canonical_task_ids=tuple(pilot_roster_tasks.values()),
            ),
            alternative_b_win_rate=preregistered_b_win_rate,
            alpha=_required_number(
                power.get("alpha"),
                field="design power alpha",
            ),
            power=target_power,
        )
        if not (
            _normalized_sha256(
                confirmation_protocol.get("powered_design_sha256")
            )
            == _sha256_canonical_json(design)
            and _normalized_sha256(
                confirmation_protocol.get("pilot_protocol_sha256")
            )
            == _normalized_sha256(
                descriptors["pilot_protocol"].get("sha256")
            )
            and _normalized_sha256(
                confirmation_protocol.get("pilot_roster_sha256")
            )
            == _normalized_sha256(
                descriptors["pilot_roster"].get("sha256")
            )
            and _normalized_sha256(
                confirmation_protocol.get("pilot_analysis_sha256")
            )
            == _normalized_sha256(
                descriptors["pilot_analysis"].get("sha256")
            )
            and math.isclose(
                _required_number(
                    confirmation_protocol.get("target_power"),
                    field="confirmation target power",
                ),
                target_power,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            and math.isclose(
                _required_number(
                    power.get("expected_discordance_rate"),
                    field="design expected discordance rate",
                ),
                confirmation_plan.conservative_discordance_rate,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            and _required_positive_int(
                power.get("required_discordant_pairs"),
                field="design required discordant pairs",
            )
            == confirmation_plan.required_discordant_pairs
            and _required_positive_int(
                power.get("required_task_count"),
                field="design required task count",
            )
            == confirmation_plan.total_unique_tasks
        ):
            return False

        confirmation_roster_document = documents["confirmation_roster"]
        confirmation_roster_tasks = _validated_roster_tasks(
            confirmation_roster_document,
            phase="confirmation",
            experiment_id=confirmation_id,
            protocol_sha256=descriptors["confirmation_protocol"].get(
                "sha256"
            ),
        )
        confirmation_roster_frozen_at_ms = _required_positive_int(
            confirmation_roster_document.get("frozen_at_ms"),
            field="confirmation roster frozen_at_ms",
        )
        first_execution_started_at_ms = min(
            assignment.first_execution_started_at_ms
            for assignment in assignments.values()
        )
        if not (
            pilot_protocol_registered_at_ms
            <= pilot_roster_frozen_at_ms
            <= pilot_completed_at_ms
            < confirmation_registered_at_ms
            <= confirmation_roster_frozen_at_ms
            < first_execution_started_at_ms
            and set(pilot_roster_tasks.values()).isdisjoint(
                confirmation_roster_tasks.values()
            )
            and confirmation_roster_tasks == confirmation_roster
            and set(confirmation_roster_tasks) == set(assignments)
            and all(
                confirmation_roster_tasks[task_id]
                == assignment.canonical_task_id
                for task_id, assignment in assignments.items()
            )
            and _normalized_sha256(
                derivation.get("pilot_analysis_sha256")
            )
            == _normalized_sha256(
                descriptors["pilot_analysis"].get("sha256")
            )
            and _normalized_sha256(
                derivation.get("confirmation_protocol_sha256")
            )
            == _normalized_sha256(
                descriptors["confirmation_protocol"].get("sha256")
            )
        ):
            return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _valid_protocol_identity(
    document: Mapping[str, Any],
    *,
    phase: str,
    experiment_id: str,
) -> bool:
    return (
        document.get("schema_version")
        == EXPERIMENT_PROTOCOL_SCHEMA_VERSION
        and _normalized_token(document.get("phase"))
        == _normalized_token(phase)
        and str(document.get("experiment_id") or "").strip()
        == experiment_id
        and _normalized_comparison(document.get("comparison")) == "b_vs_c"
    )


def _validated_roster_tasks(
    document: Mapping[str, Any],
    *,
    phase: str,
    experiment_id: str,
    protocol_sha256: Any,
) -> dict[str, str]:
    if (
        document.get("schema_version")
        != EXPERIMENT_ROSTER_SCHEMA_VERSION
        or _normalized_token(document.get("phase"))
        != _normalized_token(phase)
        or _required_text(
            document.get("experiment_id"),
            field=f"{phase} roster experiment_id",
        )
        != experiment_id
        or _normalized_sha256(document.get("protocol_sha256"))
        != _normalized_sha256(protocol_sha256)
    ):
        raise ValueError(f"{phase} roster identity mismatch")
    raw_tasks = _required_sequence(
        document.get("tasks"),
        field=f"{phase} roster tasks",
    )
    tasks: dict[str, str] = {}
    canonical_ids: set[str] = set()
    for raw_task in raw_tasks:
        task = _required_mapping(
            raw_task,
            field=f"{phase} roster task",
        )
        task_id = _required_text(
            task.get("task_id"),
            field=f"{phase} roster task_id",
        )
        task_identity = _required_mapping(
            task.get("task_identity"),
            field=f"{phase} roster task_identity",
        )
        canonical_task_id = canonical_task_identity(task_identity)
        if (
            task_id in tasks
            or canonical_task_id in canonical_ids
            or _normalized_sha256(task.get("canonical_task_id"))
            != canonical_task_id
        ):
            raise ValueError(
                f"{phase} roster tasks must be canonical, unique, and non-empty"
            )
        tasks[task_id] = canonical_task_id
        canonical_ids.add(canonical_task_id)
    if not tasks:
        raise ValueError(f"{phase} roster tasks must be non-empty")
    return tasks


def _validated_pilot_estimates(
    *,
    analysis: Mapping[str, Any],
    assignments_document: Mapping[str, Any],
    assignments_sha256: Any,
    outcomes_document: Mapping[str, Any],
    roster: Mapping[str, str],
    roster_sha256: Any,
    experiment_id: str,
) -> tuple[float, int]:
    if (
        assignments_document.get("schema_version")
        != PILOT_ASSIGNMENTS_SCHEMA_VERSION
        or _required_text(
            assignments_document.get("experiment_id"),
            field="pilot assignments experiment_id",
        )
        != experiment_id
        or _normalized_sha256(
            assignments_document.get("roster_sha256")
        )
        != _normalized_sha256(roster_sha256)
    ):
        raise ValueError("pilot assignments are not roster-bound")
    raw_assignments = _required_sequence(
        assignments_document.get("assignments"),
        field="pilot assignments",
    )
    assignments: dict[str, str] = {}
    for raw_assignment in raw_assignments:
        record = _required_mapping(
            raw_assignment,
            field="pilot assignment",
        )
        task_id = _required_text(
            record.get("task_id"),
            field="pilot assignment task_id",
        )
        if (
            task_id not in roster
            or task_id in assignments
            or _normalized_sha256(record.get("canonical_task_id"))
            != roster[task_id]
        ):
            raise ValueError("pilot assignment task identity mismatch")
        assignments[task_id] = _required_sha256(
            record.get("assignment_id"),
            field="pilot assignment_id",
        )
    if set(assignments) != set(roster):
        raise ValueError("pilot assignments omit frozen roster tasks")

    if (
        outcomes_document.get("schema_version")
        != PILOT_TERMINAL_OUTCOMES_SCHEMA_VERSION
        or _required_text(
            outcomes_document.get("experiment_id"),
            field="pilot outcomes experiment_id",
        )
        != experiment_id
        or _normalized_sha256(
            outcomes_document.get("assignments_sha256")
        )
        != _normalized_sha256(assignments_sha256)
    ):
        raise ValueError("pilot terminal outcomes are not assignment-bound")
    raw_outcomes = _required_sequence(
        outcomes_document.get("outcomes"),
        field="pilot terminal outcomes",
    )
    outcomes: dict[tuple[str, str], bool] = {}
    terminal_statuses = {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    }
    for raw_outcome in raw_outcomes:
        record = _required_mapping(
            raw_outcome,
            field="pilot terminal outcome",
        )
        task_id = _required_text(
            record.get("task_id"),
            field="pilot outcome task_id",
        )
        arm = _required_text(
            record.get("arm"),
            field="pilot outcome arm",
        )
        key = (task_id, arm)
        passed = record.get("passed")
        status = _normalized_token(record.get("status"))
        if (
            task_id not in roster
            or arm not in {"supervisor", "compute_matched_direct"}
            or key in outcomes
            or _normalized_sha256(record.get("canonical_task_id"))
            != roster[task_id]
            or _normalized_sha256(record.get("assignment_id"))
            != assignments[task_id]
            or status not in terminal_statuses
            or not isinstance(passed, bool)
            or (status != "completed" and passed)
        ):
            raise ValueError("pilot terminal outcome is invalid")
        outcomes[key] = passed
    expected_outcomes = {
        (task_id, arm)
        for task_id in roster
        for arm in ("supervisor", "compute_matched_direct")
    }
    if set(outcomes) != expected_outcomes:
        raise ValueError("pilot terminal outcomes are incomplete")

    raw_rows = _required_sequence(
        analysis.get("task_rows"),
        field="pilot task_rows",
    )
    observed_tasks: set[str] = set()
    b_wins = 0
    discordant = 0
    for raw_row in raw_rows:
        row = _required_mapping(raw_row, field="pilot task row")
        if any("attempt" in str(key).casefold() for key in row):
            raise ValueError("pilot analysis cannot use attempt-level rows")
        task_id = _required_text(
            row.get("task_id"),
            field="pilot row task_id",
        )
        task_identity = _required_mapping(
            row.get("task_identity"),
            field="pilot row task_identity",
        )
        if (
            task_id not in roster
            or task_id in observed_tasks
            or canonical_task_identity(task_identity) != roster[task_id]
            or _normalized_sha256(row.get("assignment_id"))
            != assignments[task_id]
        ):
            raise ValueError("pilot analysis row identity mismatch")
        b_pass = row.get("b_pass")
        c_pass = row.get("c_pass")
        if (
            not isinstance(b_pass, bool)
            or not isinstance(c_pass, bool)
            or b_pass
            is not outcomes[(task_id, "supervisor")]
            or c_pass
            is not outcomes[(task_id, "compute_matched_direct")]
        ):
            raise ValueError(
                "pilot analysis row disagrees with terminal outcomes"
            )
        observed_tasks.add(task_id)
        if b_pass != c_pass:
            discordant += 1
            if b_pass:
                b_wins += 1
    if observed_tasks != set(roster) or discordant <= 0:
        raise ValueError(
            "pilot analysis must include complete authoritative ITT rows"
        )
    b_win_rate = b_wins / discordant
    discordance_rate = discordant / len(roster)
    estimates = _required_mapping(
        analysis.get("estimates"),
        field="pilot analysis estimates",
    )
    if not (
        math.isclose(
            _required_number(
                estimates.get("alternative_b_win_rate"),
                field="pilot alternative B win rate",
            ),
            b_win_rate,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
        and math.isclose(
            _required_number(
                estimates.get("expected_discordance_rate"),
                field="pilot expected discordance rate",
            ),
            discordance_rate,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
    ):
        raise ValueError("pilot estimates do not match authoritative rows")
    return b_win_rate, discordant


def _parse_grade_evidence(
    document: Mapping[str, Any],
    *,
    experiment_id: str,
) -> dict[tuple[str, str], _GradeEvidence]:
    if document.get("schema_version") != B_VS_C_GRADE_SET_SCHEMA_VERSION:
        raise ValueError("unsupported grade evidence schema")
    if _required_text(
        document.get("experiment_id"),
        field="grade experiment_id",
    ) != experiment_id:
        raise ValueError("grade experiment_id mismatch")
    records = _required_sequence(document.get("grades"), field="grades")
    grades: dict[tuple[str, str], _GradeEvidence] = {}
    for raw_record in records:
        record = _required_mapping(raw_record, field="grade record")
        task_id = _required_text(
            record.get("task_id"),
            field="grade task_id",
        )
        arm = _required_text(record.get("arm"), field="grade arm")
        if arm not in {"supervisor", "compute_matched_direct"}:
            raise ValueError("grade evidence must contain only B/C arms")
        key = (task_id, arm)
        if key in grades:
            raise ValueError("duplicate task/arm grade")
        grade = _required_mapping(record.get("grade"), field="grade")
        if grade.get("schema_version") != GRADE_REVISION_SCHEMA_VERSION:
            raise ValueError("unsupported grade revision schema")
        revision_hash = _normalized_sha256(grade.get("revision_hash"))
        if revision_hash is None:
            raise ValueError("grade revision hash is missing")
        grade_payload = dict(grade)
        grade_payload.pop("revision_hash", None)
        if _sha256_canonical_json(grade_payload) != revision_hash:
            raise ValueError("grade revision hash mismatch")
        grade_id = _required_text(
            grade.get("grade_id"),
            field="grade_id",
        )
        _required_positive_int(
            grade.get("revision_number"),
            field="grade revision_number",
        )
        run_envelope = _required_mapping(
            grade.get("run_envelope"),
            field="grade run_envelope",
        )
        run_id = _required_text(
            run_envelope.get("run_id"),
            field="grade run_id",
        )
        if not _is_sha256(run_envelope.get("run_envelope_hash")):
            raise ValueError("grade run envelope is not hash pinned")
        frozen_result_hash = _normalized_sha256(
            run_envelope.get("frozen_result_hash")
        )
        if frozen_result_hash is None:
            raise ValueError("grade frozen result is not hash pinned")
        verifier = _required_mapping(
            grade.get("verifier"),
            field="grade verifier",
        )
        verifier_id = _required_text(
            verifier.get("id"),
            field="grade verifier id",
        )
        verifier_version = _required_text(
            verifier.get("version"),
            field="grade verifier version",
        )
        verifier_config_hash = _normalized_sha256(
            verifier.get("config_hash")
        )
        verifier_implementation_hash = _normalized_sha256(
            verifier.get("implementation_hash")
        )
        if (
            verifier_config_hash is None
            or verifier_implementation_hash is None
        ):
            raise ValueError("grade verifier is not hash pinned")
        passed = grade.get("passed")
        if not isinstance(passed, bool):
            raise ValueError("grade passed must be a bool")
        _required_number(grade.get("score"), field="grade score")
        _required_mapping(grade.get("evidence"), field="grade evidence")
        for field_name in (
            "failure_classification",
            "flake_classification",
        ):
            if not isinstance(grade.get(field_name), str):
                raise ValueError(f"grade {field_name} must be text")
        supersedes = grade.get("supersedes_grade_id")
        if supersedes is not None:
            _required_text(
                supersedes,
                field="grade supersedes_grade_id",
            )
        recorded_at_ms = _required_positive_int(
            grade.get("recorded_at_ms"),
            field="grade recorded_at_ms",
        )
        grades[key] = _GradeEvidence(
            task_id=task_id,
            arm=arm,
            grade_id=grade_id,
            revision_hash=revision_hash,
            run_id=run_id,
            frozen_result_hash=frozen_result_hash,
            verifier_id=verifier_id,
            verifier_version=verifier_version,
            verifier_config_hash=verifier_config_hash,
            verifier_implementation_hash=verifier_implementation_hash,
            passed=passed,
            recorded_at_ms=recorded_at_ms,
            revision_document=dict(grade),
        )
    if not grades:
        raise ValueError("grade evidence is empty")
    return grades


def _grades_are_current_in_authoritative_gradebook(
    grades: Mapping[tuple[str, str], _GradeEvidence],
    *,
    grade_authority: Any,
) -> bool:
    try:
        from .grade_revisions import DecisionGradeCitation, GradeBook

        if not isinstance(grade_authority, GradeBook):
            return False
        for grade in grades.values():
            authoritative = grade_authority.get_revision(grade.grade_id)
            if (
                authoritative.revision_hash != grade.revision_hash
                or _canonical_json(authoritative.to_dict())
                != _canonical_json(grade.revision_document)
            ):
                return False
            validation = grade_authority.validate_decision(
                (
                    DecisionGradeCitation(
                        grade_id=grade.grade_id,
                        revision_hash=grade.revision_hash,
                    ),
                )
            )
            if validation.accepted is not True:
                return False
        return True
    except Exception:
        return False


def _parse_verifier_evidence(
    document: Mapping[str, Any],
) -> _VerifierEvidence:
    if document.get("schema_version") != VERIFIER_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported verifier manifest schema")
    if (
        document.get("independent") is not True
        or document.get("hidden") is not True
    ):
        raise ValueError("verifier must be independent and hidden")
    verifier_id = _required_text(
        document.get("verifier_id"),
        field="verifier_id",
    )
    verifier_version = _required_text(
        document.get("verifier_version"),
        field="verifier_version",
    )
    verifier_config_hash = _normalized_sha256(
        document.get("verifier_config_hash")
    )
    verifier_implementation_hash = _normalized_sha256(
        document.get("verifier_implementation_hash")
    )
    if (
        verifier_config_hash is None
        or verifier_implementation_hash is None
    ):
        raise ValueError("verifier manifest is not hash pinned")
    return _VerifierEvidence(
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        verifier_config_hash=verifier_config_hash,
        verifier_implementation_hash=verifier_implementation_hash,
    )


def _ledger_covers_grade_runs(
    document: Mapping[str, Any],
    *,
    experiment_id: str,
    assignment_artifact_sha256: Any,
    grade_artifact_sha256: Any,
    grades: Mapping[tuple[str, str], _GradeEvidence],
    assignments: Mapping[str, _AssignmentEvidence],
    expected_event_payloads: Sequence[Mapping[str, Any]],
    context: _EvidenceContext,
) -> bool:
    if document.get("schema_version") != LEDGER_VERIFICATIONS_SCHEMA_VERSION:
        return False
    if str(document.get("experiment_id") or "").strip() != experiment_id:
        return False
    if _normalized_sha256(
        document.get("assignment_artifact_sha256")
    ) != _normalized_sha256(assignment_artifact_sha256):
        return False
    if _normalized_sha256(
        document.get("grade_artifact_sha256")
    ) != _normalized_sha256(grade_artifact_sha256):
        return False
    raw_runs = document.get("runs")
    if not _is_sequence(raw_runs):
        return False
    expected_run_ids = {grade.run_id for grade in grades.values()}
    expected_events_by_run: dict[str, Mapping[str, Any]] = {}
    for event in expected_event_payloads:
        run_id = str(event.get("run_id") or "").strip()
        if not run_id or run_id in expected_events_by_run:
            return False
        expected_events_by_run[run_id] = event
    if set(expected_events_by_run) != expected_run_ids:
        return False
    observed_run_ids: set[str] = set()
    for raw_run in raw_runs:
        if not isinstance(raw_run, Mapping):
            return False
        run_id = str(raw_run.get("run_id") or "").strip()
        if not run_id or run_id in observed_run_ids:
            return False
        expected_head_hash = _normalized_sha256(
            raw_run.get("expected_head_hash")
        )
        recorded_verification = raw_run.get("verification")
        event_descriptor = raw_run.get("head_event_payload")
        expected_event = expected_events_by_run.get(run_id)
        if (
            expected_head_hash is None
            or not isinstance(recorded_verification, Mapping)
            or not isinstance(event_descriptor, Mapping)
            or not isinstance(expected_event, Mapping)
            or str(event_descriptor.get("ref") or "").strip()
            != str(expected_event.get("ref") or "").strip()
            or _normalized_sha256(event_descriptor.get("sha256"))
            != _normalized_sha256(expected_event.get("sha256"))
            or str(event_descriptor.get("event_id") or "").strip()
            != str(expected_event.get("event_id") or "").strip()
            or expected_head_hash
            != _normalized_sha256(event_descriptor.get("sha256"))
        ):
            return False
        event_payload = context.resolve_json(
            event_descriptor.get("ref"),
            event_descriptor.get("sha256"),
        )
        grade = next(
            (
                candidate
                for candidate in grades.values()
                if candidate.run_id == run_id
            ),
            None,
        )
        assignment = (
            assignments.get(grade.task_id)
            if grade is not None
            else None
        )
        if (
            not isinstance(event_payload, Mapping)
            or grade is None
            or assignment is None
            or event_payload.get("schema_version")
            != "supervisor-experiment-ledger-event/v1"
            or _required_text(
                event_payload.get("experiment_id"),
                field="ledger event experiment_id",
            )
            != experiment_id
            or _required_text(
                event_payload.get("run_id"),
                field="ledger event run_id",
            )
            != run_id
            or _required_text(
                event_payload.get("task_id"),
                field="ledger event task_id",
            )
            != grade.task_id
            or _normalized_sha256(
                event_payload.get("canonical_task_id")
            )
            != assignment.canonical_task_id
            or _normalized_sha256(
                event_payload.get("assignment_id")
            )
            != assignment.assignment_id
            or _required_text(
                event_payload.get("arm"),
                field="ledger event arm",
            )
            != grade.arm
            or _normalized_sha256(
                event_payload.get("frozen_result_hash")
            )
            != grade.frozen_result_hash
            or _required_text(
                event_payload.get("grade_id"),
                field="ledger event grade_id",
            )
            != grade.grade_id
            or _normalized_sha256(
                event_payload.get("grade_revision_hash")
            )
            != grade.revision_hash
        ):
            return False
        authoritative = context.authoritative_ledger_verification(
            run_id=run_id,
            expected_head_hash=expected_head_hash,
        )
        if authoritative is None:
            return False
        authoritative_record = authoritative.to_dict()
        if _canonical_json(recorded_verification) != _canonical_json(
            authoritative_record
        ):
            return False
        external_anchor_ref = str(
            authoritative.external_anchor_ref or ""
        ).strip()
        if not (
            authoritative.valid
            and authoritative.run_id == run_id
            and authoritative.event_count > 0
            and authoritative.head_event_id is not None
            and authoritative.head_event_id
            == str(event_descriptor.get("event_id") or "").strip()
            and authoritative.head_event_hash == expected_head_hash
            and authoritative.expected_head_hash == expected_head_hash
            and authoritative.truncation_checked
            and authoritative.authoritative_head_verified
            and authoritative.failure_code is None
            and external_anchor_ref
            and urlsplit(external_anchor_ref).scheme
        ):
            return False
        observed_run_ids.add(run_id)
    return observed_run_ids == expected_run_ids


def _trace_covers_analysis_lineage(
    document: Mapping[str, Any],
    *,
    experiment_id: str,
    assignments: Mapping[str, _AssignmentEvidence],
    grades: Mapping[tuple[str, str], _GradeEvidence],
    verifier: _VerifierEvidence,
    expected_decision_canonical_key: str,
    expected_decision_revision_hash: str,
    grade_authority: Any,
) -> bool:
    try:
        graph = _trace_graph_from_document(document)
        closure = graph.validate_closure(
            now=datetime.now(timezone.utc),
            decision_grade_validator=grade_authority,
        )
    except (KeyError, TraceGraphError, TypeError, ValueError):
        return False
    if not closure.ok:
        return False
    promotion_nodes = tuple(
        node
        for node in graph.nodes
        if node.identity.node_type is NodeType.PROMOTION
    )
    if not promotion_nodes:
        return False
    try:
        for promotion in promotion_nodes:
            graph.promotion_trace(promotion.identity)
    except TraceGraphError:
        return False

    assignment_nodes: dict[tuple[str, str], list[TraceNode]] = {}
    grade_nodes: dict[
        tuple[str, str, str, str],
        list[TraceNode],
    ] = {}
    run_nodes: dict[str, list[TraceNode]] = {}
    artifact_nodes: dict[tuple[str, str], list[TraceNode]] = {}
    analysis_nodes: list[TraceNode] = []
    for node in graph.nodes:
        attributes = node.attributes
        node_type = node.identity.node_type
        if node_type is NodeType.ASN:
            task_id = str(attributes.get("task_id") or "").strip()
            assignment_id = _normalized_sha256(
                attributes.get("assignment_id")
            )
            if task_id and assignment_id is not None:
                assignment_nodes.setdefault(
                    (task_id, assignment_id),
                    [],
                ).append(node)
        elif node_type is NodeType.RUN:
            run_id = str(attributes.get("run_id") or "").strip()
            if run_id:
                run_nodes.setdefault(run_id, []).append(node)
        elif node_type is NodeType.ART:
            run_id = str(attributes.get("run_id") or "").strip()
            frozen_result_hash = _normalized_sha256(
                attributes.get("frozen_result_hash")
            )
            if run_id and frozen_result_hash is not None:
                artifact_nodes.setdefault(
                    (run_id, frozen_result_hash),
                    [],
                ).append(node)
        elif node_type is NodeType.GRADE:
            task_id = str(attributes.get("task_id") or "").strip()
            arm = str(attributes.get("arm") or "").strip()
            grade_id = str(attributes.get("grade_id") or "").strip()
            revision_hash = _normalized_sha256(
                attributes.get("grade_revision_hash")
            )
            identity_revision = node.identity.revision_hash
            if (
                task_id
                and arm
                and grade_id
                and revision_hash is not None
                and revision_hash == identity_revision
                and node.verifier_id == verifier.verifier_id
                and _normalized_sha256(
                    node.verifier_revision_hash
                )
                == verifier.verifier_implementation_hash
            ):
                grade_nodes.setdefault(
                    (task_id, arm, grade_id, revision_hash),
                    [],
                ).append(node)
        elif (
            node_type is NodeType.ANL
            and str(attributes.get("experiment_id") or "").strip()
            == experiment_id
            and _normalized_comparison(attributes.get("comparison"))
            == "b_vs_c"
        ):
            analysis_nodes.append(node)

    expected_assignment_keys = {
        (task_id, assignment.assignment_id)
        for task_id, assignment in assignments.items()
    }
    expected_grade_keys = {
        (
            grade.task_id,
            grade.arm,
            grade.grade_id,
            grade.revision_hash,
        )
        for grade in grades.values()
    }
    if (
        not analysis_nodes
        or any(
            len(assignment_nodes.get(key, ())) != 1
            for key in expected_assignment_keys
        )
        or any(
            len(grade_nodes.get(key, ())) != 1
            for key in expected_grade_keys
        )
    ):
        return False

    edge_keys = {
        (
            edge.source,
            edge.relation,
            edge.target,
        )
        for edge in graph.edges
    }
    authorizing_analysis_ids = {
        analysis.identity
        for analysis in analysis_nodes
        if any(
            edge.relation is EdgeType.DERIVED_FROM
            and edge.target == analysis.identity
            and edge.source.node_type is NodeType.DEC
            and any(
                promotion_edge.relation is EdgeType.PROMOTES
                and promotion_edge.target == edge.source
                and promotion_edge.source.node_type is NodeType.PROMOTION
                for promotion_edge in graph.edges
            )
            for edge in graph.edges
        )
    }
    if not authorizing_analysis_ids:
        return False
    authorizing_decisions = {
        edge.source
        for edge in graph.edges
        if edge.relation is EdgeType.DERIVED_FROM
        and edge.target in authorizing_analysis_ids
        and edge.source.node_type is NodeType.DEC
        and any(
            promotion_edge.relation is EdgeType.PROMOTES
            and promotion_edge.target == edge.source
            and promotion_edge.source.node_type is NodeType.PROMOTION
            for promotion_edge in graph.edges
        )
    }
    if (
        len(authorizing_decisions) != 1
        or next(iter(authorizing_decisions)).canonical_key
        != expected_decision_canonical_key
        or next(iter(authorizing_decisions)).revision_hash
        != expected_decision_revision_hash
    ):
        return False

    for grade in grades.values():
        grade_key = (
            grade.task_id,
            grade.arm,
            grade.grade_id,
            grade.revision_hash,
        )
        grade_node = grade_nodes[grade_key][0]
        assignment = assignments.get(grade.task_id)
        if assignment is None:
            return False
        assignment_node = assignment_nodes[
            (grade.task_id, assignment.assignment_id)
        ][0]
        matching_runs = [
            node
            for node in run_nodes.get(grade.run_id, ())
            if node.pinned
            and str(node.attributes.get("task_id") or "").strip()
            == grade.task_id
            and str(node.attributes.get("arm") or "").strip()
            == grade.arm
        ]
        matching_artifacts = [
            node
            for node in artifact_nodes.get(
                (grade.run_id, grade.frozen_result_hash),
                (),
            )
            if node.runtime_evidence
            and str(node.attributes.get("task_id") or "").strip()
            == grade.task_id
            and str(node.attributes.get("arm") or "").strip()
            == grade.arm
        ]
        if len(matching_runs) != 1 or len(matching_artifacts) != 1:
            return False
        run_node = matching_runs[0]
        artifact_node = matching_artifacts[0]
        if not (
            (
                grade_node.identity,
                EdgeType.EVALUATES,
                artifact_node.identity,
            )
            in edge_keys
            and (
                artifact_node.identity,
                EdgeType.DERIVED_FROM,
                run_node.identity,
            )
            in edge_keys
            and (
                run_node.identity,
                EdgeType.ASSIGNED_BY,
                assignment_node.identity,
            )
            in edge_keys
            and any(
                (
                    analysis_id,
                    EdgeType.DERIVED_FROM,
                    grade_node.identity,
                )
                in edge_keys
                for analysis_id in authorizing_analysis_ids
            )
        ):
            return False
    return True


def _trace_graph_from_document(document: Mapping[str, Any]) -> TraceGraph:
    if document.get("schema_version") != TRACE_GRAPH_SCHEMA_VERSION:
        raise TraceGraphError("unsupported trace graph schema")
    if document.get("edge_direction") != "source_record_to_prerequisite":
        raise TraceGraphError("unsupported trace edge direction")
    raw_nodes = _required_sequence(document.get("nodes"), field="trace nodes")
    raw_edges = _required_sequence(document.get("edges"), field="trace edges")
    raw_waivers = _required_sequence(
        document.get("waivers"),
        field="trace waivers",
    )
    if raw_waivers:
        raise TraceGraphError(
            "ClaimGate evidence does not accept waived trace closure"
        )

    nodes: list[TraceNode] = []
    identities: dict[str, TraceIdentity] = {}
    for raw_node in raw_nodes:
        node_payload = _required_mapping(raw_node, field="trace node")
        identity_payload = _required_mapping(
            node_payload.get("identity"),
            field="trace node identity",
        )
        identity = TraceIdentity(
            namespace=_required_text(
                identity_payload.get("namespace"),
                field="trace identity namespace",
            ),
            node_type=NodeType(
                _required_text(
                    identity_payload.get("node_type"),
                    field="trace identity node_type",
                )
            ),
            logical_id=_required_text(
                identity_payload.get("logical_id"),
                field="trace identity logical_id",
            ),
            revision_hash=_required_text(
                identity_payload.get("revision_hash"),
                field="trace identity revision_hash",
            ),
            instance_id=_required_text(
                identity_payload.get("instance_id"),
                field="trace identity instance_id",
            ),
        )
        recorded_key = identity_payload.get("canonical_key")
        if (
            recorded_key is not None
            and str(recorded_key) != identity.canonical_key
        ):
            raise TraceGraphError("trace identity canonical key mismatch")
        pinned = node_payload.get("pinned")
        runtime_evidence = node_payload.get("runtime_evidence")
        if not isinstance(pinned, bool) or not isinstance(
            runtime_evidence,
            bool,
        ):
            raise TraceGraphError("trace node flags must be booleans")
        attributes = _required_mapping(
            node_payload.get("attributes"),
            field="trace node attributes",
        )
        node = TraceNode(
            identity=identity,
            pinned=pinned,
            runtime_evidence=runtime_evidence,
            verifier_id=(
                str(node_payload["verifier_id"])
                if node_payload.get("verifier_id") is not None
                else None
            ),
            verifier_revision_hash=(
                str(node_payload["verifier_revision_hash"])
                if node_payload.get("verifier_revision_hash") is not None
                else None
            ),
            attributes=attributes,
        )
        recorded_prov_kind = node_payload.get("prov_kind")
        if (
            recorded_prov_kind is not None
            and str(recorded_prov_kind) != node.prov_kind.value
        ):
            raise TraceGraphError("trace node PROV kind mismatch")
        if identity.canonical_key in identities:
            raise TraceGraphError("duplicate trace identity")
        identities[identity.canonical_key] = identity
        nodes.append(node)

    edges: list[TraceEdge] = []
    for raw_edge in raw_edges:
        edge_payload = _required_mapping(raw_edge, field="trace edge")
        source_key = _required_text(
            edge_payload.get("source"),
            field="trace edge source",
        )
        target_key = _required_text(
            edge_payload.get("target"),
            field="trace edge target",
        )
        try:
            source = identities[source_key]
            target = identities[target_key]
        except KeyError as exc:
            raise TraceGraphError(
                "trace edge references an unknown node"
            ) from exc
        edges.append(
            TraceEdge(
                source=source,
                relation=EdgeType(
                    _required_text(
                        edge_payload.get("relation"),
                        field="trace edge relation",
                    )
                ),
                target=target,
            )
        )
    return TraceGraph(nodes=nodes, edges=edges)


def _validate_paired_task_rows(
    raw_rows: Sequence[Any],
    *,
    assignments: Mapping[str, _AssignmentEvidence],
    grades: Mapping[tuple[str, str], _GradeEvidence],
    verifier: _VerifierEvidence,
    frozen_results: Mapping[
        tuple[str, str],
        _FrozenResultEvidence,
    ],
) -> list[tuple[bool, bool]] | None:
    paired_rows: list[tuple[bool, bool]] = []
    observed_tasks: set[str] = set()
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            return None
        if any(
            "attempt" in str(key).casefold()
            for key in raw_row
        ):
            return None
        task_id = str(raw_row.get("task_id") or "").strip()
        if not task_id or task_id in observed_tasks:
            return None
        assignment = assignments.get(task_id)
        if (
            assignment is None
            or _normalized_sha256(raw_row.get("assignment_id"))
            != assignment.assignment_id
            or _normalized_sha256(raw_row.get("canonical_task_id"))
            != assignment.canonical_task_id
        ):
            return None
        b_grade = grades.get((task_id, "supervisor"))
        c_grade = grades.get((task_id, "compute_matched_direct"))
        if b_grade is None or c_grade is None:
            return None
        for prefix, grade in (("b", b_grade), ("c", c_grade)):
            frozen_result = frozen_results.get(
                (task_id, grade.arm)
            )
            if (
                frozen_result is None
                or frozen_result.canonical_task_id
                != assignment.canonical_task_id
                or frozen_result.assignment_id != assignment.assignment_id
                or frozen_result.run_id != grade.run_id
                or frozen_result.frozen_result_hash
                != grade.frozen_result_hash
                or frozen_result.passed is not grade.passed
            ):
                return None
            if str(raw_row.get(f"{prefix}_grade_id") or "").strip() != (
                grade.grade_id
            ):
                return None
            if _normalized_sha256(
                raw_row.get(f"{prefix}_grade_revision_hash")
            ) != grade.revision_hash:
                return None
            if raw_row.get(f"{prefix}_pass") is not grade.passed:
                return None
            if grade.recorded_at_ms <= (
                assignment.first_execution_started_at_ms
            ):
                return None
            if (
                grade.verifier_id != verifier.verifier_id
                or grade.verifier_version != verifier.verifier_version
                or grade.verifier_config_hash
                != verifier.verifier_config_hash
                or grade.verifier_implementation_hash
                != verifier.verifier_implementation_hash
            ):
                return None
        if (
            b_grade.grade_id == c_grade.grade_id
            or b_grade.revision_hash == c_grade.revision_hash
            or b_grade.run_id == c_grade.run_id
            or b_grade.frozen_result_hash == c_grade.frozen_result_hash
        ):
            return None
        observed_tasks.add(task_id)
        paired_rows.append((b_grade.passed, c_grade.passed))

    expected_grade_keys = {
        (task_id, arm)
        for task_id in observed_tasks
        for arm in ("supervisor", "compute_matched_direct")
    }
    if (
        not paired_rows
        or observed_tasks != set(assignments)
        or expected_grade_keys != set(grades)
        or expected_grade_keys != set(frozen_results)
    ):
        return None
    return paired_rows


def _paired_counts(
    paired_rows: Sequence[tuple[bool, bool]],
) -> dict[str, int]:
    counts = {
        "n11": 0,
        "n10": 0,
        "n01": 0,
        "n00": 0,
    }
    for b_pass, c_pass in paired_rows:
        if b_pass and c_pass:
            counts["n11"] += 1
        elif b_pass:
            counts["n10"] += 1
        elif c_pass:
            counts["n01"] += 1
        else:
            counts["n00"] += 1
    counts["task_count"] = len(paired_rows)
    counts["discordant_pairs"] = counts["n10"] + counts["n01"]
    return counts


def _validate_powered_design(
    design: Mapping[str, Any],
    *,
    task_count: int,
    discordant_pairs: int,
) -> bool:
    if (
        design.get("paired") is not True
        or _normalized_token(design.get("assignment_unit")) != "task"
        or _normalized_token(design.get("analysis_unit")) != "task"
        or _normalized_token(design.get("randomization_method"))
        != "hmac_sha256"
    ):
        return False
    key_commitment = _normalized_sha256(
        design.get("assignment_key_commitment_sha256")
    )
    treatment_hashes = design.get("treatment_hashes")
    if (
        not isinstance(design.get("assignment_version"), str)
        or not str(design.get("assignment_version")).strip()
        or key_commitment is None
        or _normalized_sha256(
            design.get("experiment_spec_hash")
        )
        is None
        or _normalized_sha256(
            design.get("task_strata_manifest_sha256")
        )
        is None
        or not isinstance(treatment_hashes, Mapping)
        or set(treatment_hashes) != set(_POWERED_DESIGN_ARM_KEYS)
        or any(
            _normalized_sha256(treatment_hashes.get(arm)) is None
            for arm in _POWERED_DESIGN_ARM_KEYS
        )
        or len(
            {
                _normalized_sha256(treatment_hashes.get(arm))
                for arm in _POWERED_DESIGN_ARM_KEYS
            }
        )
        != 3
    ):
        return False
    power = design.get("power")
    if not isinstance(power, Mapping):
        return False
    if _normalized_token(power.get("method")) != "exact_mcnemar":
        return False
    try:
        alpha = _required_number(power.get("alpha"), field="power alpha")
        target_power = _required_number(
            power.get("target_power"),
            field="target power",
        )
        alternative_b_win_rate = _required_number(
            power.get("alternative_b_win_rate"),
            field="alternative B win rate",
        )
        expected_discordance_rate = _required_number(
            power.get("expected_discordance_rate"),
            field="expected discordance rate",
        )
        required_discordant_pairs = _required_positive_int(
            power.get("required_discordant_pairs"),
            field="required discordant pairs",
        )
        required_task_count = _required_positive_int(
            power.get("required_task_count"),
            field="required task count",
        )
    except ValueError:
        return False
    if not (
        0.0 < alpha <= MAX_CONFIRMATION_ALPHA
        and MIN_CONFIRMATION_POWER <= target_power < 1.0
        and MIN_PREREGISTERED_B_WIN_RATE
        <= alternative_b_win_rate
        <= MAX_PREREGISTERED_B_WIN_RATE
        and 0.0 < expected_discordance_rate <= 1.0
        and required_discordant_pairs <= _MAX_POWER_DISCORDANT_PAIRS
    ):
        return False
    try:
        expected_discordant_pairs = exact_discordant_pairs_required(
            win_rate=alternative_b_win_rate,
            alpha=alpha,
            power=target_power,
            max_pairs=_MAX_POWER_DISCORDANT_PAIRS,
        )
    except ValueError:
        return False
    expected_task_count = math.ceil(
        required_discordant_pairs / expected_discordance_rate
    )
    return (
        required_discordant_pairs == expected_discordant_pairs
        and required_task_count == expected_task_count
        and task_count >= required_task_count
        and discordant_pairs >= required_discordant_pairs
    )


def _validate_positive_paired_result(
    result: Mapping[str, Any],
    *,
    counts: Mapping[str, int],
    alpha: float,
) -> bool:
    for field_name in (
        "task_count",
        "n11",
        "n10",
        "n01",
        "n00",
        "discordant_pairs",
    ):
        value = result.get(field_name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != counts[field_name]
        ):
            return False
    task_count = counts["task_count"]
    if task_count <= 0:
        return False
    effect = result.get("effect")
    test = result.get("test")
    if not isinstance(effect, Mapping) or not isinstance(test, Mapping):
        return False
    if (
        _normalized_token(effect.get("metric"))
        != "paired_risk_difference"
        or _normalized_token(effect.get("direction")) != "b_over_c"
    ):
        return False
    expected_effect = (counts["n10"] - counts["n01"]) / task_count
    try:
        declared_effect = _required_number(
            effect.get("estimate"),
            field="effect estimate",
        )
        declared_alpha = _required_number(
            test.get("alpha"),
            field="test alpha",
        )
        declared_p_value = _required_number(
            test.get("p_value"),
            field="test p_value",
        )
    except ValueError:
        return False
    expected_p_value = exact_mcnemar_p_value(
        n10=counts["n10"],
        n01=counts["n01"],
    )
    return (
        _normalized_token(test.get("method"))
        == "exact_mcnemar_two_sided"
        and test.get("reject_null") is True
        and math.isclose(
            declared_effect,
            expected_effect,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        and declared_effect > 0.0
        and math.isclose(
            declared_alpha,
            alpha,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
        and math.isclose(
            declared_p_value,
            expected_p_value,
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
        and 0.0 <= declared_p_value <= declared_alpha
    )


def _evidence_context(
    *,
    evidence_root: str | Path | None,
    evidence_resolver: EvidenceResolver | None,
    ledger_verification_resolver: LedgerVerificationResolver | None,
    grade_authority: Any | None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None,
    trusted_external_authorities: TrustedExternalAuthorities | None,
) -> _EvidenceContext:
    if evidence_root is not None and evidence_resolver is not None:
        raise ClaimGateError(
            "provide either evidence_root or evidence_resolver, not both"
        )
    if evidence_resolver is not None:
        if not callable(evidence_resolver):
            raise ClaimGateError("evidence_resolver must be callable")
        return _EvidenceContext(
            resolver=evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=dict(
                trusted_verifier_attestors or {}
            ),
            trusted_external_authorities=dict(
                trusted_external_authorities or {}
            ),
        )
    if evidence_root is None:
        return _EvidenceContext(
            resolver=None,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=dict(
                trusted_verifier_attestors or {}
            ),
            trusted_external_authorities=dict(
                trusted_external_authorities or {}
            ),
        )
    try:
        root = Path(evidence_root).expanduser().resolve()
    except (OSError, RuntimeError, TypeError):
        return _EvidenceContext(
            resolver=None,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=dict(
                trusted_verifier_attestors or {}
            ),
            trusted_external_authorities=dict(
                trusted_external_authorities or {}
            ),
        )
    if not root.is_dir():
        return _EvidenceContext(
            resolver=None,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=dict(
                trusted_verifier_attestors or {}
            ),
            trusted_external_authorities=dict(
                trusted_external_authorities or {}
            ),
        )
    return _EvidenceContext(
        resolver=_filesystem_evidence_resolver(root),
        ledger_verification_resolver=ledger_verification_resolver,
        grade_authority=grade_authority,
        trusted_verifier_attestors=dict(
            trusted_verifier_attestors or {}
        ),
        trusted_external_authorities=dict(
            trusted_external_authorities or {}
        ),
    )


def _filesystem_evidence_resolver(root: Path) -> EvidenceResolver:
    def resolve(reference: str) -> bytes | None:
        candidate = Path(reference).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        if not resolved.is_file():
            return None
        return resolved.read_bytes()

    return resolve


def _derived_claim_flags(level: ClaimLevel | None) -> dict[str, bool]:
    improvement_allowed = _level_at_least(level, ClaimLevel.L3)
    return {
        "improvement_claim_allowed": improvement_allowed,
        "powered_improvement_claim_allowed": improvement_allowed,
    }


def _validate_report_for_level(
    report: Mapping[str, Any],
    *,
    actual_level: ClaimLevel | None,
) -> None:
    asserted_level = report.get("asserted_claim_level")
    if asserted_level is not None:
        required_level = _parse_claim_level(asserted_level)
        if not _level_at_least(actual_level, required_level):
            raise UnsupportedClaimError(
                "asserted claim level "
                f"{required_level.value} exceeds evidence support "
                f"{_level_text(actual_level)}"
            )

    claims = report.get("claims") or []
    if isinstance(claims, (str, bytes, bytearray)) or not isinstance(
        claims,
        Sequence,
    ):
        raise UnsupportedClaimError("report claims must be a sequence")
    for claim in claims:
        rule, display = _claim_rule(claim)
        if not _level_at_least(actual_level, rule.required_level):
            raise UnsupportedClaimError(
                f"{display} requires {rule.required_level.value}; "
                f"evidence supports {_level_text(actual_level)}"
            )
    for path, claim in _iter_structured_claim_fields(report):
        rule, display = _claim_rule(claim)
        if not _level_at_least(actual_level, rule.required_level):
            raise UnsupportedClaimError(
                f"{path} {display} requires {rule.required_level.value}; "
                f"evidence supports {_level_text(actual_level)}"
            )
    for text in _iter_text_values(report):
        matched = _registered_claim_in_text(text)
        if matched is None:
            continue
        rule, display = matched
        if not _level_at_least(actual_level, rule.required_level):
            raise UnsupportedClaimError(
                f"{display} requires {rule.required_level.value}; "
                f"evidence supports {_level_text(actual_level)}"
            )


def _is_hashed_artifact(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(str(value.get("ref") or "").strip())
        and _is_sha256(value.get("sha256"))
    )


def _strict_json_loads(payload: bytes) -> Any:
    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, nested in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = nested
        return value

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )


def _required_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _required_sequence(value: Any, *, field: str) -> Sequence[Any]:
    if not _is_sequence(value):
        raise ValueError(f"{field} must be a sequence")
    return value


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _required_positive_int(value: Any, *, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(f"{field} must be a positive integer")
    return value


def _required_nonnegative_int(value: Any, *, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _required_number(value: Any, *, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _required_nonnegative_number(value: Any, *, field: str) -> float:
    result = _required_number(value, field=field)
    if result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def _numbers_close(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def _normalized_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip(
        "_"
    )


def _normalized_comparison(value: Any) -> str:
    return _normalized_token(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_canonical_json(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _evidence_bundle_sha256(evidence: Mapping[str, Any]) -> str:
    try:
        return _sha256_canonical_json(evidence)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ClaimGateError(
            "evidence bundle is not canonically JSON-serializable: "
            f"{exc}"
        ) from exc


def _is_sha256(value: Any) -> bool:
    return _normalized_sha256(value) is not None


def _model_family_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


def _normalized_sha256(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64:
        return None
    if not all(character in "0123456789abcdef" for character in text):
        return None
    return text


def _level_at_least(
    actual: ClaimLevel | None,
    required: ClaimLevel,
) -> bool:
    if actual is None:
        return False
    levels = tuple(ClaimLevel)
    return levels.index(actual) >= levels.index(required)


def _parse_claim_level(value: Any) -> ClaimLevel:
    try:
        return ClaimLevel(str(value))
    except ValueError as exc:
        raise UnsupportedClaimError(f"unknown claim level: {value}") from exc


def _managed_claim_field_paths(value: Any, *, path: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text in MANAGED_CLAIM_FIELDS:
                paths.append(nested_path)
            paths.extend(_managed_claim_field_paths(nested, path=nested_path))
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, nested in enumerate(value):
            nested_path = f"{path}[{index}]" if path else f"[{index}]"
            paths.extend(_managed_claim_field_paths(nested, path=nested_path))
    return paths


def _managed_claim_field_values(
    value: Any,
    *,
    path: str = "",
) -> list[tuple[str, bool]]:
    values: list[tuple[str, bool]] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text in MANAGED_CLAIM_FIELDS:
                values.append((nested_path, nested))
            values.extend(
                _managed_claim_field_values(nested, path=nested_path)
            )
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, nested in enumerate(value):
            nested_path = f"{path}[{index}]" if path else f"[{index}]"
            values.extend(
                _managed_claim_field_values(nested, path=nested_path)
            )
    return values


def _without_managed_claim_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_managed_claim_fields(nested)
            for key, nested in value.items()
            if str(key) not in MANAGED_CLAIM_FIELDS
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            _without_managed_claim_fields(nested)
            for nested in value
        ]
    return value


def _reject_managed_claim_fields(report: Mapping[str, Any]) -> None:
    manual_paths = _managed_claim_field_paths(report)
    if manual_paths:
        raise ManualClaimFlagError(
            f"{manual_paths[0]} is derived by ClaimGate"
        )


def _claim_rule(claim: Any) -> tuple[ClaimRule, str]:
    if isinstance(claim, Mapping):
        claim_id = str(claim.get("claim_id") or claim.get("id") or "").strip()
        claim_text = str(claim.get("text") or claim.get("claim") or "").strip()
        if claim_text:
            raise UnsupportedClaimError(
                "governed report claims must use registered claim IDs; "
                "render canonical text from the registry"
            )
    else:
        claim_id = str(claim or "").strip()
        claim_text = ""

    for rule in DEFAULT_CLAIM_RULES:
        if claim_id == rule.claim_id:
            return rule, claim_id
    display = claim_text or claim_id or "<empty claim>"
    raise UnsupportedClaimError(f"unregistered report claim: {display}")


def _iter_structured_claim_fields(
    value: Any,
    *,
    path: str = "",
    seen: set[int] | None = None,
):
    """Yield explicit claim-bearing fields that must contain registry IDs."""
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for raw_key, nested in value.items():
            key = str(raw_key)
            nested_path = f"{path}.{key}" if path else key
            normalized = _normalized_token(key)
            is_claim_field = (
                normalized in STRUCTURED_CLAIM_FIELD_NAMES
                or (
                    normalized.endswith("_claim")
                    and normalized
                    not in {
                        "improvement_claim",
                        "powered_improvement_claim",
                    }
                )
            )
            if is_claim_field and (
                isinstance(nested, str)
                or (
                    isinstance(nested, Mapping)
                    and any(
                        field in nested
                        for field in ("claim_id", "id", "text", "claim")
                    )
                )
            ):
                yield nested_path, nested
            yield from _iter_structured_claim_fields(
                nested,
                path=nested_path,
                seen=seen,
            )
        return
    if _is_sequence(value):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for index, nested in enumerate(value):
            nested_path = f"{path}[{index}]" if path else f"[{index}]"
            yield from _iter_structured_claim_fields(
                nested,
                path=nested_path,
                seen=seen,
            )


def _iter_text_values(
    value: Any,
    *,
    seen: set[int] | None = None,
):
    if isinstance(value, str):
        yield value
        return
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, nested in value.items():
            yield from _iter_text_values(key, seen=seen)
            yield from _iter_text_values(nested, seen=seen)
        return
    if _is_sequence(value):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for nested in value:
            yield from _iter_text_values(nested, seen=seen)


def _registered_claim_in_text(
    text: str,
) -> tuple[ClaimRule, str] | None:
    stripped = text.strip()
    matches: list[tuple[ClaimRule, str]] = []
    for rule in DEFAULT_CLAIM_RULES:
        if stripped == rule.claim_id:
            matches.append((rule, stripped))
        for pattern in rule.patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is not None:
                matches.append((rule, match.group(0)))
                break
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: tuple(ClaimLevel).index(item[0].required_level),
    )


def _level_text(level: ClaimLevel | None) -> str:
    return level.value if level is not None else "no claim level"
