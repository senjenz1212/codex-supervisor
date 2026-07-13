"""Evidence-derived claim authorization for supervisor reports."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


CLAIM_GATE_SCHEMA_VERSION = "supervisor-claim-gate/v1"
MANAGED_CLAIM_FIELDS = (
    "improvement_claim_allowed",
    "powered_improvement_claim_allowed",
)
EvidenceResolver = Callable[[str], bytes | bytearray | memoryview | None]


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


@dataclass(frozen=True)
class ClaimRule:
    """One registered report claim and its minimum evidence level."""

    claim_id: str
    required_level: ClaimLevel
    patterns: tuple[str, ...]


@dataclass
class _EvidenceContext:
    resolver: EvidenceResolver | None
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
    ) -> dict[str, bool]:
        level = cls.max_claim_level(
            evidence_bundle or {},
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
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
    ) -> dict[str, Any]:
        manual_paths = _managed_claim_field_paths(report)
        if manual_paths:
            raise ManualClaimFlagError(
                f"{manual_paths[0]} is derived by ClaimGate"
            )
        evidence = dict(evidence_bundle or {})
        context = _evidence_context(
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
        )
        level = cls._max_claim_level(evidence, context=context)
        _validate_report_for_level(report, actual_level=level)
        return {
            **dict(report),
            **_derived_claim_flags(level),
            "claim_gate": {
                "schema_version": CLAIM_GATE_SCHEMA_VERSION,
                "max_claim_level": level.value if level is not None else None,
                "evidence_bundle_sha256": _sha256_json(evidence),
                "derived_fields": list(MANAGED_CLAIM_FIELDS),
            },
        }

    @classmethod
    def validate_report(
        cls,
        report: Mapping[str, Any],
        evidence_bundle: Mapping[str, Any] | None = None,
        *,
        evidence_root: str | Path | None = None,
        evidence_resolver: EvidenceResolver | None = None,
    ) -> ClaimLevel | None:
        evidence = dict(evidence_bundle or {})
        actual_level = cls.max_claim_level(
            evidence,
            evidence_root=evidence_root,
            evidence_resolver=evidence_resolver,
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
        return (
            isinstance(verifier, Mapping)
            and verifier.get("independent") is True
            and verifier.get("hidden") is True
            and bool(str(verifier.get("verifier_id") or "").strip())
            and context.matches(
                verifier.get("result_ref"),
                verifier.get("result_sha256"),
            )
        )

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
        normalized_comparison = comparison.lower().replace("-", "_").replace(" ", "_")
        return (
            isinstance(result, Mapping)
            and normalized_comparison == "b_vs_c"
            and result.get("randomized") is True
            and result.get("powered") is True
            and result.get("supports_improvement") is True
            and context.matches(
                result.get("analysis_ref"),
                result.get("analysis_sha256"),
            )
        )

    @staticmethod
    def _has_strata_replication(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        replication = evidence_bundle.get("strata_replication")
        strata = replication.get("strata") if isinstance(replication, Mapping) else None
        model_families = (
            replication.get("model_families")
            if isinstance(replication, Mapping)
            else None
        )
        distinct_strata = (
            {str(value).strip() for value in strata if str(value).strip()}
            if isinstance(strata, Sequence)
            and not isinstance(strata, (str, bytes, bytearray))
            else set()
        )
        family_records = model_families if (
            isinstance(model_families, Sequence)
            and not isinstance(model_families, (str, bytes, bytearray))
        ) else ()
        pinned_families: set[str] = set()
        optimizer_seen_families: set[str] = set()
        optimizer_unseen_families: set[str] = set()
        for item in family_records:
            if not isinstance(item, Mapping) or item.get("pinned") is not True:
                continue
            family = _model_family_name(item.get("family"))
            if not family:
                continue
            pinned_families.add(family)
            if item.get("seen_by_optimizer") is True:
                optimizer_seen_families.add(family)
            if item.get("seen_by_optimizer") is False:
                optimizer_unseen_families.add(family)
        distinct_optimizer_unseen_families = (
            optimizer_unseen_families - optimizer_seen_families
        )
        return (
            isinstance(replication, Mapping)
            and replication.get("replicated") is True
            and len(distinct_strata) >= 2
            and len(pinned_families) >= 3
            and bool(
                distinct_optimizer_unseen_families.intersection(
                    pinned_families
                )
            )
            and context.matches(
                replication.get("analysis_ref"),
                replication.get("analysis_sha256"),
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
        return (
            isinstance(operating_cost, Mapping)
            and operating_cost.get("measured") is True
            and isinstance(raw_cost, (int, float))
            and not isinstance(raw_cost, bool)
            and raw_cost >= 0
            and operating_cost.get("supports_positive_roi") is True
            and context.matches(
                operating_cost.get("analysis_ref"),
                operating_cost.get("analysis_sha256"),
            )
        )

    @staticmethod
    def _has_auto_improvement_controls(
        evidence_bundle: Mapping[str, Any],
        *,
        context: _EvidenceContext,
    ) -> bool:
        return all(
            _is_control_evidence(
                evidence_bundle.get(key),
                state_key=state_key,
                context=context,
            )
            for key, state_key in (
                ("frozen_control", "frozen"),
                ("sealed_holdout", "sealed"),
                ("canary", "passed"),
            )
        )


def _evidence_context(
    *,
    evidence_root: str | Path | None,
    evidence_resolver: EvidenceResolver | None,
) -> _EvidenceContext:
    if evidence_root is not None and evidence_resolver is not None:
        raise ClaimGateError(
            "provide either evidence_root or evidence_resolver, not both"
        )
    if evidence_resolver is not None:
        if not callable(evidence_resolver):
            raise ClaimGateError("evidence_resolver must be callable")
        return _EvidenceContext(evidence_resolver)
    if evidence_root is None:
        return _EvidenceContext(None)
    try:
        root = Path(evidence_root).expanduser().resolve()
    except (OSError, RuntimeError, TypeError):
        return _EvidenceContext(None)
    if not root.is_dir():
        return _EvidenceContext(None)
    return _EvidenceContext(_filesystem_evidence_resolver(root))


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


def _is_hashed_artifact(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(str(value.get("ref") or "").strip())
        and _is_sha256(value.get("sha256"))
    )


def _is_control_evidence(
    value: Any,
    *,
    state_key: str,
    context: _EvidenceContext,
) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get(state_key) is True
        and context.matches(value.get("ref"), value.get("sha256"))
    )


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


def _claim_rule(claim: Any) -> tuple[ClaimRule, str]:
    if isinstance(claim, Mapping):
        claim_id = str(claim.get("claim_id") or claim.get("id") or "").strip()
        claim_text = str(claim.get("text") or claim.get("claim") or "").strip()
    else:
        claim_id = ""
        claim_text = str(claim or "").strip()

    matched_rules: dict[str, ClaimRule] = {}
    normalized = claim_text.casefold()
    for rule in DEFAULT_CLAIM_RULES:
        if claim_id == rule.claim_id or claim_text == rule.claim_id:
            matched_rules[rule.claim_id] = rule
        if any(re.search(pattern, normalized) for pattern in rule.patterns):
            matched_rules[rule.claim_id] = rule
    if matched_rules:
        rule = max(
            matched_rules.values(),
            key=lambda item: tuple(ClaimLevel).index(item.required_level),
        )
        return rule, claim_text or claim_id
    display = claim_text or claim_id or "<empty claim>"
    raise UnsupportedClaimError(f"unregistered report claim: {display}")


def _level_text(level: ClaimLevel | None) -> str:
    return level.value if level is not None else "no claim level"


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()
