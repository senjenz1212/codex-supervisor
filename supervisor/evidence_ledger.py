"""Tamper-evident event-chain, checkpoint, and artifact helpers.

Storage event identifiers are deliberately excluded from the event hash
preimage because SQLite assigns them during insertion. Ordering is
authenticated by a contiguous per-run event sequence and the previous-event
hash chain; authoritative checkpoints separately bind the exact persisted
event identifiers.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import inspect
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, TypeVar
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - secure filesystem writes are POSIX-only
    fcntl = None  # type: ignore[assignment]

from .redaction import redact, redact_v1, redact_v2
from .run_manifest import capture_acceptance_evidence
from .trace_envelope import stamp_trace_envelope


LEGACY_EVENT_HASH_SCHEMA_VERSION = "evidence-ledger-event/v2"
EVENT_HASH_SCHEMA_VERSION = "evidence-ledger-event/v3"
LEGACY_REDACTION_RULES_VERSION = "supervisor-redaction-rules/v1"
REDACTION_RULES_VERSION = "supervisor-redaction-rules/v2"
_FROZEN_REDACTION_RULES_BY_VERSION = MappingProxyType({
    LEGACY_REDACTION_RULES_VERSION: redact_v1,
    REDACTION_RULES_VERSION: redact_v2,
})
_FROZEN_EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA = MappingProxyType({
    LEGACY_EVENT_HASH_SCHEMA_VERSION: LEGACY_REDACTION_RULES_VERSION,
    EVENT_HASH_SCHEMA_VERSION: REDACTION_RULES_VERSION,
})
_FROZEN_EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA = MappingProxyType({
    LEGACY_EVENT_HASH_SCHEMA_VERSION: frozenset({
        LEGACY_EVENT_HASH_SCHEMA_VERSION,
    }),
    EVENT_HASH_SCHEMA_VERSION: frozenset({
        LEGACY_EVENT_HASH_SCHEMA_VERSION,
        EVENT_HASH_SCHEMA_VERSION,
    }),
})
# Compatibility/extension registries remain mutable for additive future
# schemas. Frozen historical entries always take precedence during resolution.
REDACTION_RULES_BY_VERSION: dict[str, Callable[[Any], Any]] = dict(
    _FROZEN_REDACTION_RULES_BY_VERSION
)
EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA: dict[str, str] = dict(
    _FROZEN_EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA
)
EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA: dict[
    str,
    frozenset[str],
] = dict(_FROZEN_EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA)
EVENT_IDENTITY_SCHEMA_VERSION = "evidence-ledger-event-identity/v1"
EVENT_IDENTITY_CHAIN_SCOPE = "event-id-chain/v1"
EVENT_IDENTITY_HEAD_SCOPE = "head-event/v1"
EVIDENCE_COMMIT_EVENT_KIND = "harness.evidence.committed"
EVIDENCE_COMMIT_EVENT_SOURCE = "evidence_committer"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "evidence-ledger-artifact-manifest/v1"
ARTIFACT_MANIFEST_ATTESTATION_SCHEMA_VERSION = (
    "evidence-ledger-artifact-manifest-attestation/v1"
)
LEGACY_RAW_PAYLOAD_COMMITMENT_SCHEMA_VERSION = (
    "evidence-ledger-legacy-raw-payload/v1"
)
CHECKPOINT_SCHEMA_VERSION = "evidence-ledger-checkpoint/v2"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
CHECKPOINT_PREDICATE_TYPE = (
    "https://codex-supervisor.local/attestations/evidence-ledger-checkpoint/v1"
)
ARTIFACT_MANIFEST_PREDICATE_TYPE = (
    "https://codex-supervisor.local/attestations/artifact-manifest/v1"
)
NATIVE_GENESIS = "native"
LEGACY_IMPORT_GENESIS = "legacy-import"
GENESIS_KINDS = frozenset({NATIVE_GENESIS, LEGACY_IMPORT_GENESIS})
_ARTIFACT_DESCRIPTOR_KEYS = frozenset(
    {"name", "digest", "size", "media_type", "uri"}
)
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


class LedgerError(RuntimeError):
    """Base error for evidence-ledger operations."""


class ArtifactIntegrityError(LedgerError):
    """A content-addressed artifact did not match its digest."""


class CheckpointSigner(Protocol):
    """Minimal signer interface accepted by :func:`create_ledger_checkpoint`."""

    def sign(self, payload: bytes) -> Mapping[str, Any] | bytes | str:
        ...


Signer = CheckpointSigner | Callable[[bytes], Mapping[str, Any] | bytes | str]
ProjectionT = TypeVar("ProjectionT")


@dataclass(frozen=True)
class LedgerFields:
    event_sequence: int
    previous_event_hash: str | None
    event_hash: str
    canonical_payload_hash: str
    artifact_manifest_hash: str
    ledger_genesis_kind: str | None


@dataclass(frozen=True)
class LedgerVerification:
    valid: bool
    run_id: str | None
    event_count: int
    head_event_id: Any | None
    head_event_hash: str | None
    expected_head_hash: str | None
    truncation_checked: bool
    head_event_identity_hash: str | None = None
    expected_event_identity_hash: str | None = None
    authoritative_head_verified: bool = False
    external_anchor_ref: str | None = None
    failure_code: str | None = None
    failure_event_id: Any | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "run_id": self.run_id,
            "event_count": self.event_count,
            "head_event_id": self.head_event_id,
            "head_event_hash": self.head_event_hash,
            "expected_head_hash": self.expected_head_hash,
            "truncation_checked": self.truncation_checked,
            "head_event_identity_hash": self.head_event_identity_hash,
            "expected_event_identity_hash": self.expected_event_identity_hash,
            "authoritative_head_verified": self.authoritative_head_verified,
            "external_anchor_ref": self.external_anchor_ref,
            "failure_code": self.failure_code,
            "failure_event_id": self.failure_event_id,
            "detail": self.detail,
        }


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON deterministically and reject non-finite numeric values."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def strict_json_object_loads(raw_json: str | bytes) -> dict[str, Any]:
    """Decode one JSON object while rejecting duplicate keys at every depth."""
    if isinstance(raw_json, bytes):
        raw_text = raw_json.decode("utf-8")
    elif isinstance(raw_json, str):
        raw_text = raw_json
    else:
        raise TypeError("JSON payload must be text or bytes")
    loaded = json.loads(
        raw_text,
        object_pairs_hook=_json_object_without_duplicate_keys,
    )
    if not isinstance(loaded, dict):
        raise TypeError("JSON payload must be an object")
    return loaded


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compute_event_identity_hash(
    *,
    run_id: str,
    event_sequence: int,
    event_id: int,
    event_hash: str,
    previous_event_identity_hash: str | None,
) -> str:
    """Commit one storage ID into a stable per-run identity chain."""
    sequence = _require_exact_integer(
        event_sequence,
        field="event_sequence",
        minimum=1,
    )
    storage_id = _require_exact_integer(
        event_id,
        field="event_id",
        minimum=1,
    )
    normalized_event_hash = _require_canonical_sha256(event_hash)
    previous_identity = (
        None
        if previous_event_identity_hash is None
        else _require_canonical_sha256(previous_event_identity_hash)
    )
    return sha256_hex(
        canonical_json_bytes(
            {
                "schema_version": EVENT_IDENTITY_SCHEMA_VERSION,
                "run_id": _require_exact_text(run_id, field="run_id"),
                "event_sequence": sequence,
                "event_id": storage_id,
                "event_hash": normalized_event_hash,
                "previous_event_identity_hash": previous_identity,
            }
        )
    )


def compute_head_event_identity_hash(
    *,
    run_id: str,
    event_count: int,
    head_event_id: int,
    head_event_hash: str,
) -> str:
    """Compatibility commitment for callers that only possess a stream head."""
    return sha256_hex(
        canonical_json_bytes(
            {
                "schema_version": (
                    "evidence-ledger-head-event-identity/v1"
                ),
                "run_id": _require_exact_text(run_id, field="run_id"),
                "event_count": _require_exact_integer(
                    event_count,
                    field="event_count",
                    minimum=1,
                ),
                "head_event_id": _require_exact_integer(
                    head_event_id,
                    field="head_event_id",
                    minimum=1,
                ),
                "head_event_hash": _require_canonical_sha256(
                    head_event_hash
                ),
            }
        )
    )


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(dict(payload)))


def _redactor_for_event_hash_schema(
    schema_version: str,
    _frozen_schema_rules: Mapping[str, str] = (
        _FROZEN_EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA
    ),
    _frozen_redactors: Mapping[str, Callable[[Any], Any]] = (
        _FROZEN_REDACTION_RULES_BY_VERSION
    ),
) -> Callable[[Any], Any]:
    rules_version = _frozen_schema_rules.get(schema_version)
    if rules_version is None:
        rules_version = EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA.get(
            schema_version
        )
    if rules_version is None:
        raise ValueError(
            f"unsupported event-hash schema: {schema_version}"
        )
    redactor = _frozen_redactors.get(rules_version)
    if redactor is None:
        redactor = REDACTION_RULES_BY_VERSION.get(rules_version)
    if redactor is None:
        raise ValueError(
            "event-hash schema references unsupported redaction rules: "
            f"{schema_version} -> {rules_version}"
        )
    return redactor


def _supported_event_hash_schema_versions(
    _frozen_versions: tuple[str, ...] = tuple(
        _FROZEN_EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA
    ),
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *_frozen_versions,
                *EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA,
            )
        )
    )


def _allowed_event_hash_schema_predecessors(
    schema_version: str,
    _frozen_transitions: Mapping[str, frozenset[str]] = (
        _FROZEN_EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA
    ),
) -> frozenset[str] | None:
    frozen = _frozen_transitions.get(schema_version)
    if frozen is not None:
        return frozen
    return EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA.get(
        schema_version
    )


def supported_event_hash_schema_versions() -> tuple[str, ...]:
    """Return frozen historical schemas followed by additive extensions."""
    return _supported_event_hash_schema_versions()


def event_hash_schema_transition_allowed(
    previous_schema_version: str,
    current_schema_version: str,
) -> bool:
    allowed = _allowed_event_hash_schema_predecessors(
        current_schema_version
    )
    return (
        allowed is not None
        and previous_schema_version in allowed
    )


def prepare_event_payload(
    *,
    run_id: str,
    source: str,
    kind: str,
    payload: Mapping[str, Any],
    event_hash_schema_version: str = EVENT_HASH_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Stamp trace context, then redact before any payload hash or write."""
    event_payload = dict(payload)
    if (
        kind == "dual_agent_gate_result"
        and "acceptance_evidence" not in event_payload
    ):
        acceptance_evidence = capture_acceptance_evidence(event_payload)
        if acceptance_evidence is not None:
            event_payload["acceptance_evidence"] = acceptance_evidence
    redactor = _redactor_for_event_hash_schema(
        event_hash_schema_version
    )
    return redactor(
        stamp_trace_envelope(
            run_id=run_id,
            source=source,
            kind=kind,
            payload=event_payload,
        )
    )


def artifact_manifest_hash(manifest: Mapping[str, Any]) -> str:
    body = dict(manifest)
    body.pop("manifest_hash", None)
    return sha256_hex(canonical_json_bytes(body))


def build_artifact_manifest(
    artifacts: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = [redact(dict(artifact)) for artifact in artifacts]
    normalized.sort(key=canonical_json_text)
    body: dict[str, Any] = {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "artifacts": normalized,
    }
    if metadata:
        body["metadata"] = redact(dict(metadata))
    return {
        **body,
        "manifest_hash": artifact_manifest_hash(body),
    }


def _validate_artifact_manifest(
    manifest: Mapping[str, Any],
) -> tuple[tuple[str, str, int, str, str], ...]:
    required_keys = {
        "schema_version",
        "artifacts",
        "manifest_hash",
    }
    allowed_keys = required_keys | {"metadata"}
    if (
        not isinstance(manifest, Mapping)
        or not required_keys.issubset(manifest)
        or set(manifest) - allowed_keys
        or manifest.get("schema_version")
        != ARTIFACT_MANIFEST_SCHEMA_VERSION
    ):
        raise ArtifactIntegrityError(
            "artifact manifest has a noncanonical shape"
        )
    try:
        declared_hash = _require_canonical_sha256(
            manifest.get("manifest_hash")
        )
    except ValueError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc
    if artifact_manifest_hash(manifest) != declared_hash:
        raise ArtifactIntegrityError("artifact manifest hash mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ArtifactIntegrityError(
            "artifact manifest artifacts must be a list"
        )
    if "metadata" in manifest and not isinstance(
        manifest.get("metadata"),
        Mapping,
    ):
        raise ArtifactIntegrityError(
            "artifact manifest metadata must be an object"
        )
    descriptors: list[tuple[str, str, int, str, str]] = []
    seen_names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ArtifactIntegrityError(
                "artifact descriptor must be an object"
            )
        descriptor = _validate_artifact_descriptor(artifact)
        if descriptor[0] in seen_names:
            raise ArtifactIntegrityError(
                "artifact manifest contains a duplicate descriptor"
            )
        seen_names.add(descriptor[0])
        descriptors.append(descriptor)
    return tuple(descriptors)


def create_artifact_manifest_attestation(
    manifest: Mapping[str, Any],
    *,
    manifest_bytes: bytes,
    signer: Signer,
    created_at: int,
    manifest_name: str = "artifact-manifest.json",
    signer_provider_id: str | None = None,
) -> dict[str, Any]:
    """Directly sign one immutable artifact manifest as an in-toto statement."""
    _validate_artifact_manifest(manifest)
    declared_hash = str(manifest["manifest_hash"])
    exact_manifest_bytes = _canonical_manifest_file_bytes(
        manifest,
        manifest_bytes,
    )
    manifest_file_sha256 = sha256_hex(exact_manifest_bytes)
    if type(created_at) is not int or created_at < 0:
        raise ValueError("artifact manifest attestation created_at is invalid")
    normalized_name = _normalize_artifact_name(manifest_name)
    signer_identity = _checkpoint_signer_identity(
        signer,
        provider_id=signer_provider_id,
    )
    statement: dict[str, Any] = {
        "_type": IN_TOTO_STATEMENT_TYPE,
        "subject": [{
            "name": normalized_name,
            "digest": {"sha256": manifest_file_sha256},
        }],
        "predicateType": ARTIFACT_MANIFEST_PREDICATE_TYPE,
        "predicate": {
            "schema_version": ARTIFACT_MANIFEST_ATTESTATION_SCHEMA_VERSION,
            "manifest_schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
            "manifest_hash": declared_hash,
            "signer_provider_id": signer_identity["provider_id"],
            "signer_key_id": signer_identity["key_id"],
            "signer_algorithm": signer_identity["algorithm"],
            "created_at": created_at,
        },
    }
    signed_payload = canonical_json_bytes(statement)
    signature = _sign_checkpoint(
        signer,
        signed_payload,
        expected_key_id=signer_identity["key_id"],
        expected_algorithm=signer_identity["algorithm"],
    )
    return {
        **statement,
        "signatures": [signature],
        "signing_payload_sha256": sha256_hex(signed_payload),
    }


def verify_artifact_manifest_attestation(
    attestation: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
    verifier: Any,
) -> bool:
    """Verify manifest identity, signed payload, signer metadata, and signature."""
    if not isinstance(attestation, Mapping) or set(attestation) != {
        "_type",
        "subject",
        "predicateType",
        "predicate",
        "signatures",
        "signing_payload_sha256",
    }:
        raise ArtifactIntegrityError(
            "artifact manifest attestation has a noncanonical shape"
        )
    if (
        attestation.get("_type") != IN_TOTO_STATEMENT_TYPE
        or attestation.get("predicateType")
        != ARTIFACT_MANIFEST_PREDICATE_TYPE
    ):
        raise ArtifactIntegrityError(
            "artifact manifest attestation type is invalid"
        )
    _validate_artifact_manifest(manifest)
    manifest_hash = str(manifest["manifest_hash"])
    exact_manifest_bytes = _canonical_manifest_file_bytes(
        manifest,
        manifest_bytes,
    )
    manifest_file_sha256 = sha256_hex(exact_manifest_bytes)
    subject = attestation.get("subject")
    if (
        not isinstance(subject, list)
        or len(subject) != 1
        or not isinstance(subject[0], Mapping)
        or set(subject[0]) != {"name", "digest"}
        or not isinstance(subject[0].get("digest"), Mapping)
        or set(subject[0]["digest"]) != {"sha256"}
        or subject[0]["digest"].get("sha256") != manifest_file_sha256
    ):
        raise ArtifactIntegrityError(
            "artifact manifest attestation file digest mismatch"
        )
    _normalize_artifact_name(subject[0].get("name"))
    predicate = attestation.get("predicate")
    expected_predicate_keys = {
        "schema_version",
        "manifest_schema_version",
        "manifest_hash",
        "signer_provider_id",
        "signer_key_id",
        "signer_algorithm",
        "created_at",
    }
    if (
        not isinstance(predicate, Mapping)
        or set(predicate) != expected_predicate_keys
        or predicate.get("schema_version")
        != ARTIFACT_MANIFEST_ATTESTATION_SCHEMA_VERSION
        or predicate.get("manifest_schema_version")
        != ARTIFACT_MANIFEST_SCHEMA_VERSION
        or predicate.get("manifest_hash") != manifest_hash
        or type(predicate.get("created_at")) is not int
        or int(predicate["created_at"]) < 0
    ):
        raise ArtifactIntegrityError(
            "artifact manifest attestation predicate is invalid"
        )
    try:
        key_id = _canonical_signer_text(
            predicate.get("signer_key_id"),
            field="key_id",
        )
        algorithm = _canonical_signer_text(
            predicate.get("signer_algorithm"),
            field="algorithm",
        )
        _canonical_signer_text(
            predicate.get("signer_provider_id"),
            field="provider_id",
        )
    except ValueError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc
    statement = {
        "_type": attestation["_type"],
        "subject": subject,
        "predicateType": attestation["predicateType"],
        "predicate": predicate,
    }
    signed_payload = canonical_json_bytes(statement)
    try:
        declared_payload_hash = _require_canonical_sha256(
            attestation.get("signing_payload_sha256")
        )
    except ValueError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc
    if sha256_hex(signed_payload) != declared_payload_hash:
        raise ArtifactIntegrityError(
            "artifact manifest attestation signing payload hash mismatch"
        )
    signatures = attestation.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ArtifactIntegrityError(
            "artifact manifest attestation signature is missing"
        )
    accepted = False
    verify = getattr(verifier, "verify", None)
    for signature in signatures:
        if (
            not isinstance(signature, Mapping)
            or signature.get("key_id") != key_id
            or signature.get("algorithm") != algorithm
            or not isinstance(signature.get("signature"), str)
            or not str(signature["signature"]).strip()
        ):
            raise ArtifactIntegrityError(
                "artifact manifest attestation signature metadata is invalid"
            )
        try:
            valid = (
                verify(signed_payload, signature)
                if callable(verify)
                else verifier(signed_payload, signature)
            )
        except Exception as exc:
            raise ArtifactIntegrityError(
                "artifact manifest attestation signature verification failed"
            ) from exc
        if inspect.isawaitable(valid):
            close = getattr(valid, "close", None)
            if callable(close):
                close()
            raise ArtifactIntegrityError(
                "artifact manifest attestation verifier must be synchronous"
            )
        accepted = valid is True or accepted
    if not accepted:
        raise ArtifactIntegrityError(
            "artifact manifest attestation signature is invalid"
        )
    return True


def _canonical_manifest_file_bytes(
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
) -> bytes:
    if not isinstance(manifest_bytes, bytes):
        raise ArtifactIntegrityError(
            "artifact manifest file bytes must be supplied exactly"
        )
    canonical = canonical_json_bytes(manifest)
    if manifest_bytes != canonical:
        raise ArtifactIntegrityError(
            "artifact manifest file bytes are not canonical JSON"
        )
    return manifest_bytes


EMPTY_ARTIFACT_MANIFEST = build_artifact_manifest(())
EMPTY_ARTIFACT_MANIFEST_HASH = str(EMPTY_ARTIFACT_MANIFEST["manifest_hash"])


def artifact_manifest_hash_for_payload(payload: Mapping[str, Any]) -> str:
    explicit = payload.get("artifact_manifest_hash")
    manifest = payload.get("artifact_manifest")
    computed: str | None = None
    if isinstance(manifest, Mapping):
        computed = artifact_manifest_hash(manifest)
        declared = manifest.get("manifest_hash")
        if (
            declared is not None
            and _require_canonical_sha256(declared) != computed
        ):
            raise ValueError("artifact manifest hash does not match manifest content")
    if computed is None:
        envelope = payload.get("trace_envelope")
        artifacts = envelope.get("artifacts") if isinstance(envelope, Mapping) else None
        if isinstance(artifacts, list) and artifacts:
            computed = str(build_artifact_manifest(artifacts)["manifest_hash"])
    if explicit is not None:
        explicit_hash = _require_canonical_sha256(explicit)
        if computed is not None and explicit_hash != computed:
            raise ValueError("payload artifact_manifest_hash does not match artifact_manifest")
        return explicit_hash
    return computed or EMPTY_ARTIFACT_MANIFEST_HASH


def legacy_raw_payload_manifest_hash(
    raw_payload_json: str | bytes,
    *,
    semantic_artifact_manifest_hash: str,
) -> str:
    """Bind preserved legacy JSON bytes and their semantic artifact manifest.

    Imported rows store this versioned, domain-separated digest in the
    ``artifact_manifest_hash`` column so the existing event-hash schema also
    authenticates their original JSON encoding.
    """
    if isinstance(raw_payload_json, str):
        raw_payload_bytes = raw_payload_json.encode("utf-8")
    elif isinstance(raw_payload_json, bytes):
        raw_payload_bytes = raw_payload_json
    else:
        raise TypeError("legacy raw payload_json must be text or bytes")
    return sha256_hex(
        canonical_json_bytes(
            {
                "schema_version": (
                    LEGACY_RAW_PAYLOAD_COMMITMENT_SCHEMA_VERSION
                ),
                "raw_payload_json_sha256": sha256_hex(raw_payload_bytes),
                "semantic_artifact_manifest_hash": (
                    _require_canonical_sha256(
                        semantic_artifact_manifest_hash
                    )
                ),
            }
        )
    )


def compute_event_hash(
    *,
    run_id: str,
    event_sequence: int,
    ts: int,
    source: str,
    kind: str,
    previous_event_hash: str | None,
    canonical_payload_hash_value: str,
    artifact_manifest_hash_value: str,
    ledger_genesis_kind: str | None,
    event_hash_schema_version: str = EVENT_HASH_SCHEMA_VERSION,
) -> str:
    sequence = _normalize_event_sequence(event_sequence)
    previous = (
        None if previous_event_hash is None else _normalize_sha256(previous_event_hash)
    )
    payload_hash = _normalize_sha256(canonical_payload_hash_value)
    manifest_hash = _normalize_sha256(artifact_manifest_hash_value)
    genesis = _normalize_genesis_kind(ledger_genesis_kind)
    if previous is None and genesis not in GENESIS_KINDS:
        raise ValueError("genesis event must declare native or legacy-import")
    if previous is not None and genesis is not None:
        raise ValueError("non-genesis event cannot declare a genesis kind")
    preimage = {
        "schema_version": str(event_hash_schema_version),
        "run_id": str(run_id),
        "event_sequence": sequence,
        "ts": int(ts),
        "source": str(source),
        "kind": str(kind),
        "previous_event_hash": previous,
        "canonical_payload_hash": payload_hash,
        "artifact_manifest_hash": manifest_hash,
        "ledger_genesis_kind": genesis,
    }
    return sha256_hex(canonical_json_bytes(preimage))


def build_ledger_fields(
    *,
    run_id: str,
    event_sequence: int,
    ts: int,
    source: str,
    kind: str,
    payload: Mapping[str, Any],
    previous_event_hash: str | None,
    ledger_genesis_kind: str | None,
    event_hash_schema_version: str = EVENT_HASH_SCHEMA_VERSION,
) -> LedgerFields:
    sequence = _normalize_event_sequence(event_sequence)
    payload_hash = canonical_payload_hash(payload)
    manifest_hash = artifact_manifest_hash_for_payload(payload)
    event_hash = compute_event_hash(
        run_id=run_id,
        event_sequence=sequence,
        ts=ts,
        source=source,
        kind=kind,
        previous_event_hash=previous_event_hash,
        canonical_payload_hash_value=payload_hash,
        artifact_manifest_hash_value=manifest_hash,
        ledger_genesis_kind=ledger_genesis_kind,
        event_hash_schema_version=event_hash_schema_version,
    )
    return LedgerFields(
        event_sequence=sequence,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
        canonical_payload_hash=payload_hash,
        artifact_manifest_hash=manifest_hash,
        ledger_genesis_kind=ledger_genesis_kind,
    )


def build_legacy_import_ledger_fields(
    *,
    run_id: str,
    event_sequence: int,
    ts: int,
    source: str,
    kind: str,
    payload: Mapping[str, Any],
    raw_payload_json: str | bytes,
    previous_event_hash: str | None,
    ledger_genesis_kind: str | None,
    event_hash_schema_version: str = EVENT_HASH_SCHEMA_VERSION,
) -> LedgerFields:
    """Build fields for an imported row without rewriting its JSON text."""
    if ledger_genesis_kind not in {None, LEGACY_IMPORT_GENESIS}:
        raise ValueError(
            "legacy import rows may only use legacy-import genesis"
        )
    decoded_raw_payload = strict_json_object_loads(raw_payload_json)
    if decoded_raw_payload != dict(payload):
        raise ValueError(
            "legacy raw payload_json does not match the decoded payload"
        )
    sequence = _normalize_event_sequence(event_sequence)
    payload_hash = canonical_payload_hash(payload)
    semantic_manifest_hash = artifact_manifest_hash_for_payload(payload)
    manifest_hash = legacy_raw_payload_manifest_hash(
        raw_payload_json,
        semantic_artifact_manifest_hash=semantic_manifest_hash,
    )
    event_hash = compute_event_hash(
        run_id=run_id,
        event_sequence=sequence,
        ts=ts,
        source=source,
        kind=kind,
        previous_event_hash=previous_event_hash,
        canonical_payload_hash_value=payload_hash,
        artifact_manifest_hash_value=manifest_hash,
        ledger_genesis_kind=ledger_genesis_kind,
        event_hash_schema_version=event_hash_schema_version,
    )
    return LedgerFields(
        event_sequence=sequence,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
        canonical_payload_hash=payload_hash,
        artifact_manifest_hash=manifest_hash,
        ledger_genesis_kind=ledger_genesis_kind,
    )


def _verify_event_chain(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    expected_head_hash: str | None = None,
    expected_event_identity_hash: str | None = None,
    expected_run_id: str | None = None,
) -> LedgerVerification:
    """Shared event-chain verifier; public callers choose an assurance level."""
    rows = list(events)
    normalized_expected = (
        None
        if expected_head_hash is None
        else _require_canonical_sha256(expected_head_hash)
    )
    normalized_expected_identity = (
        None
        if expected_event_identity_hash is None
        else _require_canonical_sha256(expected_event_identity_hash)
    )
    run_id = str(expected_run_id) if expected_run_id is not None else None
    previous_hash: str | None = None
    previous_identity_hash: str | None = None
    previous_sequence = 0
    previous_event_id = 0
    head_id: Any | None = None
    head_hash: str | None = None
    head_identity_hash: str | None = None
    payload_commitment_mode: str | None = None
    previous_event_hash_schema_version: str | None = None

    def failure(
        code: str,
        *,
        event_id: Any | None,
        detail: str,
    ) -> LedgerVerification:
        return LedgerVerification(
            valid=False,
            run_id=run_id,
            event_count=len(rows),
            head_event_id=head_id,
            head_event_hash=head_hash,
            expected_head_hash=normalized_expected,
            truncation_checked=(
                normalized_expected is not None
                or normalized_expected_identity is not None
            ),
            head_event_identity_hash=head_identity_hash,
            expected_event_identity_hash=normalized_expected_identity,
            failure_code=code,
            failure_event_id=event_id,
            detail=detail,
        )

    for index, row in enumerate(rows):
        raw_event_id = _event_value(row, "event_id")
        try:
            event_id = _require_exact_integer(
                raw_event_id,
                field="event_id",
                minimum=1,
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_event_id",
                event_id=raw_event_id,
                detail=str(exc),
            )
        if event_id <= previous_event_id:
            return failure(
                "event_id_order_mismatch",
                event_id=event_id,
                detail=(
                    "event_id must increase in the same order as "
                    "event_sequence"
                ),
            )

        row_run_id_value = _event_value(row, "run_id", run_id)
        try:
            row_run_id = _require_exact_text(
                row_run_id_value,
                field="run_id",
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_run_id",
                event_id=event_id,
                detail=str(exc),
            )
        if run_id is None:
            run_id = row_run_id
        if row_run_id != run_id:
            return failure(
                "run_id_mismatch",
                event_id=event_id,
                detail=f"expected run_id={run_id!r}, observed={row_run_id!r}",
            )
        try:
            event_sequence = _require_exact_integer(
                _event_value(row, "event_sequence"),
                field="event_sequence",
                minimum=1,
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_event_sequence",
                event_id=event_id,
                detail=str(exc),
            )
        expected_sequence = previous_sequence + 1
        if event_sequence != expected_sequence:
            return failure(
                "event_sequence_gap",
                event_id=event_id,
                detail=(
                    f"expected event_sequence={expected_sequence}, "
                    f"observed={event_sequence}"
                ),
            )
        try:
            event_ts = _require_exact_integer(
                _event_value(row, "ts"),
                field="event timestamp",
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_event_timestamp",
                event_id=event_id,
                detail=str(exc),
            )
        try:
            source = _require_exact_text(
                _event_value(row, "source"),
                field="source",
            )
            kind = _require_exact_text(
                _event_value(row, "kind"),
                field="kind",
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_event_text",
                event_id=event_id,
                detail=str(exc),
            )
        observed_previous = _event_value(row, "previous_event_hash")
        try:
            observed_genesis = _require_exact_genesis_kind(
                _event_value(row, "ledger_genesis_kind")
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_genesis_kind",
                event_id=event_id,
                detail=str(exc),
            )
        if index == 0:
            if observed_previous is not None:
                return failure(
                    "genesis_previous_hash_present",
                    event_id=event_id,
                    detail="first observed event has a previous_event_hash",
                )
            if observed_genesis not in GENESIS_KINDS:
                return failure(
                    "missing_genesis_marker",
                    event_id=event_id,
                    detail="first observed event lacks an explicit genesis marker",
                )
            payload_commitment_mode = (
                "legacy"
                if observed_genesis == LEGACY_IMPORT_GENESIS
                else "native"
            )
        else:
            try:
                normalized_previous = _require_canonical_sha256(
                    observed_previous
                )
            except ValueError as exc:
                return failure(
                    "invalid_previous_event_hash",
                    event_id=event_id,
                    detail=str(exc),
                )
            if normalized_previous != previous_hash:
                return failure(
                    "previous_event_hash_mismatch",
                    event_id=event_id,
                    detail=(
                        f"expected previous_event_hash={previous_hash}, "
                        f"observed={normalized_previous}"
                    ),
                )
            if observed_genesis is not None:
                return failure(
                    "unexpected_genesis_marker",
                    event_id=event_id,
                    detail="only the first event may carry a genesis marker",
                )
        try:
            decoded_payload = _decode_event_payload(row)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            return failure(
                "invalid_payload_json",
                event_id=event_id,
                detail=str(exc),
            )
        if (
            payload_commitment_mode == "native"
            and not decoded_payload.canonical_encoding
        ):
            return failure(
                "noncanonical_payload_json",
                event_id=event_id,
                detail="event payload_json is not canonically encoded",
            )
        payload = decoded_payload.payload
        try:
            observed_payload_hash = _require_canonical_sha256(
                _event_value(row, "canonical_payload_hash")
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_canonical_payload_hash",
                event_id=event_id,
                detail=str(exc),
            )
        try:
            observed_manifest_hash = _require_canonical_sha256(
                _event_value(row, "artifact_manifest_hash")
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_artifact_manifest_hash",
                event_id=event_id,
                detail=str(exc),
            )
        try:
            observed_event_hash = _require_canonical_sha256(
                _event_value(row, "event_hash")
            )
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_event_hash",
                event_id=event_id,
                detail=str(exc),
            )

        computed_payload_hash = canonical_payload_hash(payload)
        if observed_payload_hash != computed_payload_hash:
            return failure(
                "canonical_payload_hash_mismatch",
                event_id=event_id,
                detail=(
                    f"expected canonical_payload_hash={computed_payload_hash}, "
                    f"observed={observed_payload_hash}"
                ),
            )
        try:
            semantic_manifest_hash = artifact_manifest_hash_for_payload(payload)
        except (TypeError, ValueError) as exc:
            return failure(
                "invalid_artifact_manifest_hash",
                event_id=event_id,
                detail=str(exc),
            )
        computed_manifest_hash = semantic_manifest_hash
        if payload_commitment_mode == "legacy":
            # Every imported row uses the raw commitment. The first canonical
            # row with the ordinary manifest hash closes the import epoch, so
            # later native rows cannot fall back to legacy encoding rules.
            if decoded_payload.raw_json_bytes is None:
                if observed_manifest_hash != semantic_manifest_hash:
                    return failure(
                        "legacy_raw_payload_commitment_missing",
                        event_id=event_id,
                        detail=(
                            "legacy-import row lacks payload_json bytes and "
                            "does not use the canonical native commitment"
                        ),
                    )
                payload_commitment_mode = "native"
            else:
                legacy_manifest_hash = legacy_raw_payload_manifest_hash(
                    decoded_payload.raw_json_bytes,
                    semantic_artifact_manifest_hash=semantic_manifest_hash,
                )
                if observed_manifest_hash == legacy_manifest_hash:
                    computed_manifest_hash = legacy_manifest_hash
                elif (
                    decoded_payload.canonical_encoding
                    and observed_manifest_hash == semantic_manifest_hash
                ):
                    payload_commitment_mode = "native"
                else:
                    return failure(
                        "legacy_raw_payload_commitment_mismatch",
                        event_id=event_id,
                        detail=(
                            "preserved legacy payload_json bytes do not match "
                            "the stored versioned commitment"
                        ),
                    )
        elif observed_manifest_hash != semantic_manifest_hash:
            return failure(
                "artifact_manifest_hash_mismatch",
                event_id=event_id,
                detail=(
                    f"expected artifact_manifest_hash={semantic_manifest_hash}, "
                    f"observed={observed_manifest_hash}"
                ),
            )
        matching_event_hashes: dict[str, str] = {}
        for schema_version in _supported_event_hash_schema_versions():
            try:
                candidate_hash = compute_event_hash(
                    run_id=run_id or "",
                    event_sequence=event_sequence,
                    ts=event_ts,
                    source=source,
                    kind=kind,
                    previous_event_hash=(
                        None if index == 0 else normalized_previous
                    ),
                    canonical_payload_hash_value=computed_payload_hash,
                    artifact_manifest_hash_value=computed_manifest_hash,
                    ledger_genesis_kind=observed_genesis,
                    event_hash_schema_version=schema_version,
                )
            except (TypeError, ValueError):
                continue
            if secrets.compare_digest(
                observed_event_hash,
                candidate_hash,
            ):
                matching_event_hashes[schema_version] = candidate_hash
        if len(matching_event_hashes) != 1:
            return failure(
                "unsupported_event_hash_schema",
                event_id=event_id,
                detail=(
                    "event hash does not identify exactly one supported "
                    "event-hash schema"
                ),
            )
        event_hash_schema_version, computed_event_hash = next(
            iter(matching_event_hashes.items())
        )
        if previous_event_hash_schema_version is not None:
            if not event_hash_schema_transition_allowed(
                previous_event_hash_schema_version,
                event_hash_schema_version,
            ):
                return failure(
                    "event_hash_schema_transition_not_allowed",
                    event_id=event_id,
                    detail=(
                        "event-hash schema transition is not allowed: "
                        f"{previous_event_hash_schema_version} -> "
                        f"{event_hash_schema_version}"
                    ),
                )
        try:
            redactor = _redactor_for_event_hash_schema(
                event_hash_schema_version
            )
        except ValueError as exc:
            return failure(
                "unsupported_event_hash_schema",
                event_id=event_id,
                detail=str(exc),
            )
        try:
            redacted_payload = redactor(payload)
        except (TypeError, ValueError) as exc:
            return failure(
                "payload_not_redacted",
                event_id=event_id,
                detail=(
                    "persisted payload cannot be safely normalized by the "
                    "event-hash schema's bound redactor: "
                    f"{event_hash_schema_version}: {exc}"
                ),
            )
        if redacted_payload != payload:
            return failure(
                "payload_not_redacted",
                event_id=event_id,
                detail=(
                    "persisted payload contains data that the event-hash "
                    f"schema's bound redactor would change: "
                    f"{event_hash_schema_version}"
                ),
            )
        computed_identity_hash = compute_event_identity_hash(
            run_id=run_id or "",
            event_sequence=event_sequence,
            event_id=event_id,
            event_hash=computed_event_hash,
            previous_event_identity_hash=previous_identity_hash,
        )
        previous_hash = computed_event_hash
        previous_event_hash_schema_version = event_hash_schema_version
        previous_identity_hash = computed_identity_hash
        previous_sequence = event_sequence
        previous_event_id = event_id
        head_id = event_id
        head_hash = computed_event_hash
        head_identity_hash = computed_identity_hash

    if normalized_expected is not None and head_hash != normalized_expected:
        return failure(
            "expected_head_hash_mismatch",
            event_id=head_id,
            detail=f"expected head={normalized_expected}, observed={head_hash}",
        )
    if (
        normalized_expected_identity is not None
        and head_identity_hash != normalized_expected_identity
    ):
        return failure(
            "expected_event_identity_hash_mismatch",
            event_id=head_id,
            detail=(
                "expected event identity head="
                f"{normalized_expected_identity}, "
                f"observed={head_identity_hash}"
            ),
        )
    return LedgerVerification(
        valid=True,
        run_id=run_id,
        event_count=len(rows),
        head_event_id=head_id,
        head_event_hash=head_hash,
        expected_head_hash=normalized_expected,
        truncation_checked=(
            normalized_expected is not None
            or normalized_expected_identity is not None
        ),
        head_event_identity_hash=head_identity_hash,
        expected_event_identity_hash=normalized_expected_identity,
    )


def verify_event_chain_structure(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    expected_run_id: str | None = None,
) -> LedgerVerification:
    """Verify local hash-chain structure without claiming tail completeness.

    This API is intentionally diagnostic-only. A valid result proves that the
    observed rows form an internally consistent prefix; it cannot prove that
    later rows were not truncated.
    """
    return _verify_event_chain(
        events,
        expected_run_id=expected_run_id,
    )


def verify_event_chain(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    expected_head_hash: str | None = None,
    expected_event_identity_hash: str | None = None,
    expected_run_id: str | None = None,
) -> LedgerVerification:
    """Verify a chain against a caller-supplied trusted expected head.

    Release and promotion callers must source ``expected_head_hash`` from a
    rollback-independent trust domain. Without one, this fail-closed API never
    upgrades an internally valid prefix into accepted evidence; use
    :func:`verify_event_chain_structure` for local diagnostics.
    """
    verification = _verify_event_chain(
        events,
        expected_head_hash=expected_head_hash,
        expected_event_identity_hash=expected_event_identity_hash,
        expected_run_id=expected_run_id,
    )
    if (
        expected_head_hash is not None
        or expected_event_identity_hash is not None
        or not verification.valid
    ):
        return verification
    return replace(
        verification,
        valid=False,
        failure_code="trusted_head_required",
        detail=(
            "release-grade verification requires an expected head from a "
            "rollback-independent trust domain"
        ),
    )


def rebuild_projection(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    initial: ProjectionT,
    reducer: Callable[[ProjectionT, dict[str, Any]], ProjectionT],
    expected_head_hash: str | None = None,
    expected_event_identity_hash: str | None = None,
    expected_run_id: str | None = None,
) -> ProjectionT:
    """Rebuild a JSON projection from a verified event stream deterministically.

    The helper canonical-round-trips both the initial value and final projection
    so logically equivalent mappings produce byte-identical serialized output.
    Callers that need truncation resistance must provide ``expected_head_hash``
    or use the authoritative checkpoint verifier before rebuilding.
    """
    rows = list(events)
    verification = verify_event_chain(
        rows,
        expected_head_hash=expected_head_hash,
        expected_event_identity_hash=expected_event_identity_hash,
        expected_run_id=expected_run_id,
    )
    if not verification.valid:
        raise LedgerError(
            "cannot rebuild projection from an invalid event chain: "
            f"{verification.failure_code}: {verification.detail}"
        )

    projection = json.loads(canonical_json_text(initial))
    for row in rows:
        event = {
            "event_id": _event_value(row, "event_id"),
            "run_id": _event_value(row, "run_id"),
            "event_sequence": _event_value(row, "event_sequence"),
            "ts": int(_event_value(row, "ts")),
            "source": str(_event_value(row, "source")),
            "kind": str(_event_value(row, "kind")),
            "payload": _event_payload(row),
            "previous_event_hash": _event_value(row, "previous_event_hash"),
            "event_hash": _event_value(row, "event_hash"),
            "canonical_payload_hash": _event_value(
                row,
                "canonical_payload_hash",
            ),
            "artifact_manifest_hash": _event_value(
                row,
                "artifact_manifest_hash",
            ),
            "ledger_genesis_kind": _event_value(row, "ledger_genesis_kind"),
        }
        projection = reducer(projection, event)
    return json.loads(canonical_json_text(projection))


def create_ledger_checkpoint(
    *,
    run_id: str,
    head_event_id: Any,
    head_event_hash: str,
    event_count: int,
    external_anchor_ref: str,
    signer: Signer,
    created_at: int,
    event_identity_hash: str | None = None,
    previous_checkpoint_hash: str | None = None,
    signer_provider_id: str | None = None,
) -> dict[str, Any]:
    """Create a signed in-toto-shaped checkpoint for an externally anchored head."""
    anchor = str(external_anchor_ref).strip()
    if not anchor or not urlsplit(anchor).scheme:
        raise ValueError("external_anchor_ref must be an absolute URI")
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")
    normalized_head_event_id = _require_exact_integer(
        head_event_id,
        field="head_event_id",
        minimum=1,
    )
    if isinstance(event_count, bool) or not isinstance(event_count, int):
        raise ValueError("event_count must be an integer")
    normalized_count = event_count
    if normalized_count <= 0:
        raise ValueError("event_count must be positive")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise ValueError("created_at must be an integer")
    normalized_head = _normalize_sha256(head_event_hash)
    signer_identity = _checkpoint_signer_identity(
        signer,
        provider_id=signer_provider_id,
    )
    if event_identity_hash is None:
        normalized_event_identity_hash = compute_head_event_identity_hash(
            run_id=normalized_run_id,
            event_count=normalized_count,
            head_event_id=normalized_head_event_id,
            head_event_hash=normalized_head,
        )
        event_identity_scope = EVENT_IDENTITY_HEAD_SCOPE
    else:
        normalized_event_identity_hash = _require_canonical_sha256(
            event_identity_hash
        )
        event_identity_scope = EVENT_IDENTITY_CHAIN_SCOPE
    normalized_previous_checkpoint_hash = (
        None
        if previous_checkpoint_hash is None
        else _require_canonical_sha256(previous_checkpoint_hash)
    )
    statement: dict[str, Any] = {
        "_type": IN_TOTO_STATEMENT_TYPE,
        "subject": [
            {
                "name": f"evidence-ledger/run/{normalized_run_id}",
                "digest": {"sha256": normalized_head},
            }
        ],
        "predicateType": CHECKPOINT_PREDICATE_TYPE,
        "predicate": {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "head_event_id": normalized_head_event_id,
            "head_event_hash": normalized_head,
            "event_count": normalized_count,
            "event_identity_scope": event_identity_scope,
            "event_identity_hash": normalized_event_identity_hash,
            "previous_checkpoint_hash": normalized_previous_checkpoint_hash,
            "signer_provider_id": signer_identity["provider_id"],
            "signer_key_id": signer_identity["key_id"],
            "signer_algorithm": signer_identity["algorithm"],
            "created_at": created_at,
            "external_anchor_ref": anchor,
        },
    }
    signed_payload = canonical_json_bytes(statement)
    signature = _sign_checkpoint(
        signer,
        signed_payload,
        expected_key_id=signer_identity["key_id"],
        expected_algorithm=signer_identity["algorithm"],
    )
    return {
        **statement,
        "signatures": [signature],
        "signing_payload_sha256": sha256_hex(signed_payload),
    }


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | _NOFOLLOW | _DIRECTORY | _CLOEXEC
_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | _CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
_APPEND_ONLY_TEMP_FILENAME = re.compile(
    r"^\.tmp-[1-9][0-9]*-[0-9a-f]{32}$"
)


def _absolute_no_follow_path(path: str | Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    if absolute.parent == absolute:
        return absolute
    return absolute.parent.resolve(strict=False) / absolute.name


def _validate_path_component(
    name: str,
    *,
    error_type: type[LedgerError],
    label: str,
) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise error_type(f"{label} is not a safe path component: {name!r}")


def _secure_open_error(
    *,
    error_type: type[LedgerError],
    label: str,
    error: OSError,
) -> LedgerError:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return error_type(
            f"{label} contains a symlink or non-directory path component"
        )
    if error.errno == errno.ENOENT:
        return error_type(f"{label} is missing")
    return error_type(f"{label} could not be opened safely: {error}")


def _require_no_follow_support(error_type: type[LedgerError]) -> None:
    if not _NOFOLLOW or not _DIRECTORY:
        raise error_type("secure no-follow filesystem operations are unavailable")


def _open_directory_tree(
    path: str | Path,
    *,
    create: bool,
    error_type: type[LedgerError],
    label: str,
) -> int:
    """Open an absolute directory path one component at a time without links."""
    _require_no_follow_support(error_type)
    absolute = _absolute_no_follow_path(path)
    anchor = absolute.anchor
    if not anchor:
        raise error_type(f"{label} must resolve to an absolute path")
    try:
        current_fd = os.open(anchor, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise _secure_open_error(
            error_type=error_type,
            label=f"{label} anchor",
            error=exc,
        ) from exc

    try:
        for component in absolute.parts[1:]:
            _validate_path_component(
                component,
                error_type=error_type,
                label=label,
            )
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _secure_open_error(
                        error_type=error_type,
                        label=label,
                        error=exc,
                    ) from exc
            try:
                child_fd = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                raise _secure_open_error(
                    error_type=error_type,
                    label=label,
                    error=exc,
                ) from exc
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


@contextmanager
def _directory_tree_fd(
    path: str | Path,
    *,
    create: bool,
    error_type: type[LedgerError],
    label: str,
) -> Iterator[int]:
    descriptor = _open_directory_tree(
        path,
        create=create,
        error_type=error_type,
        label=label,
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    create: bool,
    error_type: type[LedgerError],
    label: str,
    missing_ok: bool = False,
) -> int | None:
    _validate_path_component(name, error_type=error_type, label=label)
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise _secure_open_error(
                error_type=error_type,
                label=label,
                error=exc,
            ) from exc
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise error_type(f"{label} is missing")
    except OSError as exc:
        raise _secure_open_error(
            error_type=error_type,
            label=label,
            error=exc,
        ) from exc


def _read_regular_file_at(
    parent_fd: int,
    name: str,
    *,
    error_type: type[LedgerError],
    label: str,
    require_single_link: bool = True,
    max_bytes: int | None = None,
) -> bytes:
    _validate_path_component(name, error_type=error_type, label=label)
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise _secure_open_error(
            error_type=error_type,
            label=label,
            error=exc,
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise error_type(f"{label} is not a regular file")
        if require_single_link and metadata.st_nlink != 1:
            raise error_type(f"{label} has unexpected hard links")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise error_type(f"{label} exceeds the maximum allowed size")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


@contextmanager
def _append_only_directory_lock(
    parent_fd: int,
    *,
    error_type: type[LedgerError],
    label: str,
) -> Iterator[None]:
    """Serialize one append-only directory across threads and processes."""
    if fcntl is None:
        raise error_type(
            f"{label} cross-process filesystem locking is unavailable"
        )
    while True:
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_EX)
            break
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise error_type(
                f"{label} cross-process lock failed: {exc}"
            ) from exc
    try:
        yield
    finally:
        while True:
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
                break
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise error_type(
                    f"{label} cross-process unlock failed: {exc}"
                ) from exc


def _fsync_append_only_directory(
    parent_fd: int,
    *,
    error_type: type[LedgerError],
    label: str,
) -> None:
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise error_type(
            f"{label} directory fsync failed: {exc}"
        ) from exc


def _recover_append_only_temporary_files_at(
    parent_fd: int,
    *,
    error_type: type[LedgerError],
    label: str,
) -> None:
    """Remove abandoned publication temps while holding the directory lock."""
    try:
        names = sorted(os.listdir(parent_fd))
    except OSError as exc:
        raise error_type(
            f"{label} directory could not be listed safely: {exc}"
        ) from exc
    temporary_names = [
        name for name in names if _APPEND_ONLY_TEMP_FILENAME.fullmatch(name)
    ]
    if not temporary_names:
        return

    metadata_by_name: dict[str, os.stat_result] = {}
    for name in names:
        try:
            metadata_by_name[name] = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise _secure_open_error(
                error_type=error_type,
                label=f"{label} entry {name!r}",
                error=exc,
            ) from exc

    for temporary_name in temporary_names:
        metadata = metadata_by_name[temporary_name]
        if not stat.S_ISREG(metadata.st_mode):
            raise error_type(
                f"{label} temporary entry {temporary_name!r} "
                "is not a regular file"
            )
        if metadata.st_nlink not in {1, 2}:
            raise error_type(
                f"{label} temporary entry {temporary_name!r} "
                "has unexpected hard links"
            )
        if metadata.st_nlink == 2:
            linked_names = [
                name
                for name, candidate in metadata_by_name.items()
                if name != temporary_name
                and not _APPEND_ONLY_TEMP_FILENAME.fullmatch(name)
                and candidate.st_dev == metadata.st_dev
                and candidate.st_ino == metadata.st_ino
            ]
            if len(linked_names) != 1:
                raise error_type(
                    f"{label} temporary entry {temporary_name!r} "
                    "does not have exactly one published peer"
                )
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise error_type(
                f"{label} temporary entry {temporary_name!r} "
                f"could not be removed: {exc}"
            ) from exc

    _fsync_append_only_directory(
        parent_fd,
        error_type=error_type,
        label=f"{label} temporary cleanup",
    )


def _append_only_file_at(
    parent_fd: int,
    name: str,
    value: bytes,
    *,
    error_type: type[LedgerError],
    label: str,
    _lock_held: bool = False,
) -> tuple[bool, bytes | None]:
    """Atomically create ``name`` without following or replacing filesystem links."""
    _validate_path_component(name, error_type=error_type, label=label)
    if _APPEND_ONLY_TEMP_FILENAME.fullmatch(name):
        raise error_type(f"{label} conflicts with reserved temporary names")
    if not _lock_held:
        with _append_only_directory_lock(
            parent_fd,
            error_type=error_type,
            label=label,
        ):
            return _append_only_file_at(
                parent_fd,
                name,
                value,
                error_type=error_type,
                label=label,
                _lock_held=True,
            )

    _recover_append_only_temporary_files_at(
        parent_fd,
        error_type=error_type,
        label=label,
    )
    temporary_name = f".tmp-{os.getpid()}-{secrets.token_hex(16)}"
    try:
        descriptor = os.open(
            temporary_name,
            _WRITE_FLAGS,
            0o600,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise _secure_open_error(
            error_type=error_type,
            label=f"{label} temporary file",
            error=exc,
        ) from exc

    try:
        try:
            remaining = memoryview(value)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise error_type(f"{label} write made no progress")
                remaining = remaining[written:]
            os.fsync(descriptor)
        except OSError as exc:
            raise _secure_open_error(
                error_type=error_type,
                label=label,
                error=exc,
            ) from exc
        finally:
            os.close(descriptor)

        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = _read_regular_file_at(
                parent_fd,
                name,
                error_type=error_type,
                label=label,
            )
            return False, existing
        except OSError as exc:
            raise _secure_open_error(
                error_type=error_type,
                label=label,
                error=exc,
            ) from exc
        _fsync_append_only_directory(
            parent_fd,
            error_type=error_type,
            label=label,
        )
        return True, None
    finally:
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except OSError:
            pass


def _normalize_artifact_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ArtifactIntegrityError("artifact descriptor name must be a string")
    name = value.strip()
    if (
        not name
        or name != value
        or len(name) > 1024
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise ArtifactIntegrityError(
            "artifact descriptor name is not canonical"
        )
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != name
    ):
        raise ArtifactIntegrityError(
            "artifact descriptor name is not a safe relative name"
        )
    return name


def _normalize_artifact_media_type(value: Any) -> str:
    if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
        raise ArtifactIntegrityError(
            "artifact descriptor media type is not canonical"
        )
    return value


def _validate_artifact_descriptor(
    artifact: Mapping[str, Any],
) -> tuple[str, str, int, str, str]:
    if set(artifact) != _ARTIFACT_DESCRIPTOR_KEYS:
        raise ArtifactIntegrityError(
            "artifact descriptor has a noncanonical shape"
        )
    name = _normalize_artifact_name(artifact.get("name"))
    digest_field = artifact.get("digest")
    if (
        not isinstance(digest_field, Mapping)
        or set(digest_field) != {"sha256"}
    ):
        raise ArtifactIntegrityError(
            "artifact descriptor digest has a noncanonical shape"
        )
    try:
        digest = _require_canonical_sha256(digest_field.get("sha256"))
    except ValueError as exc:
        raise ArtifactIntegrityError(str(exc)) from exc
    size = artifact.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ArtifactIntegrityError(
            "artifact descriptor size must be a nonnegative integer"
        )
    media_type = _normalize_artifact_media_type(
        artifact.get("media_type")
    )
    uri = artifact.get("uri")
    expected_uri = f"cas:sha256:{digest}"
    if not isinstance(uri, str) or uri != expected_uri:
        raise ArtifactIntegrityError(
            "artifact descriptor URI is not canonical for its digest"
        )
    return name, digest, size, media_type, uri


class ContentAddressedArtifactStore:
    """Sha256-addressed bytes stored beneath a no-follow directory boundary."""

    def __init__(self, root: str | Path):
        self.root = _absolute_no_follow_path(root)
        with _directory_tree_fd(
            self.root,
            create=True,
            error_type=ArtifactIntegrityError,
            label="artifact store root",
        ) as root_fd:
            sha256_fd = _open_child_directory(
                root_fd,
                "sha256",
                create=True,
                error_type=ArtifactIntegrityError,
                label="artifact store sha256 directory",
            )
            if sha256_fd is not None:
                os.close(sha256_fd)

    def put_bytes(
        self,
        data: bytes,
        *,
        name: str | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        value = bytes(data)
        digest = sha256_hex(value)
        artifact_name = _normalize_artifact_name(
            name or f"sha256:{digest}"
        )
        artifact_media_type = _normalize_artifact_media_type(media_type)
        with self._artifact_parent_fd(digest, create=True) as parent_fd:
            created, existing = _append_only_file_at(
                parent_fd,
                digest,
                value,
                error_type=ArtifactIntegrityError,
                label=f"artifact sha256:{digest}",
            )
            if not created and (
                existing is None
                or existing != value
                or sha256_hex(existing) != digest
            ):
                raise ArtifactIntegrityError(
                    f"artifact path content does not match digest: {digest}"
                )
        return {
            "name": artifact_name,
            "digest": {"sha256": digest},
            "size": len(value),
            "media_type": artifact_media_type,
            "uri": f"cas:sha256:{digest}",
        }

    def put_file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        source = _absolute_no_follow_path(path)
        with _directory_tree_fd(
            source.parent,
            create=False,
            error_type=ArtifactIntegrityError,
            label="artifact source parent",
        ) as parent_fd:
            value = _read_regular_file_at(
                parent_fd,
                source.name,
                error_type=ArtifactIntegrityError,
                label="artifact source file",
                require_single_link=False,
            )
        return self.put_bytes(
            value,
            name=name or source.name,
            media_type=media_type,
        )

    def read_bytes(self, digest: str) -> bytes:
        normalized = _normalize_sha256(digest)
        with self._artifact_parent_fd(normalized, create=False) as parent_fd:
            with _append_only_directory_lock(
                parent_fd,
                error_type=ArtifactIntegrityError,
                label=f"artifact sha256:{normalized}",
            ):
                _recover_append_only_temporary_files_at(
                    parent_fd,
                    error_type=ArtifactIntegrityError,
                    label=f"artifact sha256:{normalized}",
                )
                value = _read_regular_file_at(
                    parent_fd,
                    normalized,
                    error_type=ArtifactIntegrityError,
                    label=f"artifact sha256:{normalized}",
                )
        if sha256_hex(value) != normalized:
            raise ArtifactIntegrityError(
                f"artifact content does not match digest: {normalized}"
            )
        return value

    def create_manifest(
        self,
        artifacts: Sequence[Mapping[str, Any]],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = build_artifact_manifest(artifacts, metadata=metadata)
        self.verify_manifest(manifest)
        manifest_body_bytes = canonical_json_bytes(
            {
                key: value
                for key, value in manifest.items()
                if key != "manifest_hash"
            }
        )
        # The v1 manifest hash commits to the body without its self-hash.
        manifest_body = self.put_bytes(
            manifest_body_bytes,
            name="artifact-manifest.json",
            media_type="application/vnd.codex-supervisor.artifact-manifest+json",
        )
        if (
            manifest_body["digest"]["sha256"]
            != manifest["manifest_hash"]
        ):
            raise ArtifactIntegrityError(
                "artifact manifest hash does not address canonical body bytes"
            )
        # Attestations bind the full manifest file, so retain those bytes too.
        self.put_bytes(
            canonical_json_bytes(manifest),
            name="artifact-manifest.json",
            media_type="application/vnd.codex-supervisor.artifact-manifest+json",
        )
        return manifest

    def create_manifest_attestation(
        self,
        manifest: Mapping[str, Any],
        *,
        signer: Signer,
        verifier: Any,
        created_at: int,
        manifest_name: str = "artifact-manifest.json",
        signer_provider_id: str | None = None,
    ) -> dict[str, Any]:
        self.verify_manifest(manifest)
        manifest_bytes = canonical_json_bytes(manifest)
        attestation = create_artifact_manifest_attestation(
            manifest,
            manifest_bytes=manifest_bytes,
            signer=signer,
            created_at=created_at,
            manifest_name=manifest_name,
            signer_provider_id=signer_provider_id,
        )
        verify_artifact_manifest_attestation(
            attestation,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            verifier=verifier,
        )
        self.put_bytes(
            canonical_json_bytes(attestation),
            name="artifact-manifest.attestation.json",
            media_type=(
                "application/vnd.codex-supervisor."
                "artifact-manifest-attestation+json"
            ),
        )
        return attestation

    def verify_manifest(self, manifest: Mapping[str, Any]) -> bool:
        descriptors = _validate_artifact_manifest(manifest)
        for _name, digest, declared_size, _media_type, _uri in descriptors:
            value = self.read_bytes(digest)
            if len(value) != declared_size:
                raise ArtifactIntegrityError(
                    "artifact descriptor size does not match stored bytes"
                )
        return True

    @contextmanager
    def _artifact_parent_fd(
        self,
        digest: str,
        *,
        create: bool,
    ) -> Iterator[int]:
        normalized = _normalize_sha256(digest)
        with _directory_tree_fd(
            self.root,
            create=False,
            error_type=ArtifactIntegrityError,
            label="artifact store root",
        ) as root_fd:
            sha256_fd = _open_child_directory(
                root_fd,
                "sha256",
                create=False,
                error_type=ArtifactIntegrityError,
                label="artifact store sha256 directory",
            )
            assert sha256_fd is not None
            try:
                parent_fd = _open_child_directory(
                    sha256_fd,
                    normalized[:2],
                    create=create,
                    error_type=ArtifactIntegrityError,
                    label=f"artifact digest parent {normalized[:2]}",
                )
                assert parent_fd is not None
                try:
                    yield parent_fd
                finally:
                    os.close(parent_fd)
            finally:
                os.close(sha256_fd)

    def _path(self, digest: str) -> Path:
        normalized = _normalize_sha256(digest)
        return self.root / "sha256" / normalized[:2] / normalized


def _event_value(event: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        return event[key]
    except (KeyError, IndexError, TypeError):
        return default


_MISSING = object()


@dataclass(frozen=True)
class _DecodedEventPayload:
    payload: dict[str, Any]
    raw_json_bytes: bytes | None
    canonical_encoding: bool


def _decode_event_payload(
    event: Mapping[str, Any] | Any,
) -> _DecodedEventPayload:
    payload = _event_value(event, "payload", _MISSING)
    raw = _event_value(event, "payload_json", _MISSING)
    if isinstance(raw, Mapping):
        loaded = dict(raw)
        canonical_json_bytes(loaded)
        raw_json_bytes = None
        canonical_encoding = True
    elif raw is not _MISSING:
        if isinstance(raw, bytes):
            raw_json_bytes = raw
            raw_text = raw.decode("utf-8")
        elif isinstance(raw, str):
            raw_text = raw
            raw_json_bytes = raw.encode("utf-8")
        else:
            raise TypeError("event payload_json must be JSON text or an object")
        loaded = strict_json_object_loads(raw_text)
        canonical_encoding = canonical_json_bytes(loaded) == raw_json_bytes
    elif isinstance(payload, Mapping):
        loaded = dict(payload)
        canonical_json_bytes(loaded)
        raw_json_bytes = None
        canonical_encoding = True
    else:
        raise TypeError("event payload must be a JSON object")

    if payload is not _MISSING:
        if not isinstance(payload, Mapping):
            raise TypeError("event payload must be a JSON object")
        if dict(payload) != loaded:
            raise ValueError("event payload and payload_json differ")
    return _DecodedEventPayload(
        payload=loaded,
        raw_json_bytes=raw_json_bytes,
        canonical_encoding=canonical_encoding,
    )


def _event_payload(event: Mapping[str, Any] | Any) -> dict[str, Any]:
    return _decode_event_payload(event).payload


def _require_exact_text(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be a string")
    return value


def _require_exact_integer(
    value: Any,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} is below the supported range")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} is above the supported range")
    return value


def _require_canonical_sha256(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(
            f"sha256 digest must be exactly 64 lowercase hex characters: {value!r}"
        )
    return value


def _require_exact_genesis_kind(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value not in GENESIS_KINDS:
        raise ValueError(
            "ledger_genesis_kind must be exactly native, legacy-import, or null"
        )
    return value


def _canonical_signer_text(value: Any, *, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"checkpoint signer {field} is not canonical")
    return value


def _checkpoint_signer_identity(
    signer: Signer,
    *,
    provider_id: str | None,
) -> dict[str, str]:
    key_id = _canonical_signer_text(
        getattr(signer, "key_id", None),
        field="key_id",
    )
    algorithm = _canonical_signer_text(
        getattr(signer, "algorithm", None),
        field="algorithm",
    )
    provider = _canonical_signer_text(
        (
            provider_id
            if provider_id is not None
            else getattr(signer, "provider_id", key_id)
        ),
        field="provider_id",
    )
    return {
        "provider_id": provider,
        "key_id": key_id,
        "algorithm": algorithm,
    }


def _normalize_sha256(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if len(raw) != 64 or any(ch not in "0123456789abcdef" for ch in raw):
        raise ValueError(f"invalid sha256 digest: {value!r}")
    return raw


def _normalize_event_sequence(value: Any) -> int:
    return _require_exact_integer(
        value,
        field="event_sequence",
        minimum=1,
    )


def _normalize_genesis_kind(value: Any) -> str | None:
    return _require_exact_genesis_kind(value)


def _sign_checkpoint(
    signer: Signer,
    payload: bytes,
    *,
    expected_key_id: str | None = None,
    expected_algorithm: str | None = None,
) -> dict[str, Any]:
    default_key_id = _canonical_signer_text(
        getattr(signer, "key_id", None),
        field="key_id",
    )
    default_algorithm = _canonical_signer_text(
        getattr(signer, "algorithm", None),
        field="algorithm",
    )
    sign = getattr(signer, "sign", None)
    result = sign(payload) if callable(sign) else signer(payload)  # type: ignore[misc]
    defaults = {
        "key_id": default_key_id,
        "algorithm": default_algorithm,
    }
    if isinstance(result, Mapping):
        signature = {**defaults, **dict(result)}
        if "signature" not in signature and "value" in signature:
            signature["signature"] = signature.pop("value")
        if not signature.get("signature"):
            raise ValueError("checkpoint signer result must include signature")
    else:
        if isinstance(result, bytes):
            encoded = base64.b64encode(result).decode("ascii")
        elif isinstance(result, str):
            encoded = result
        else:
            raise TypeError(
                "checkpoint signer must return a mapping, bytes, or string"
            )
        signature = {
            **defaults,
            "signature": encoded,
        }
    signature_key_id = _canonical_signer_text(
        signature.get("key_id"),
        field="key_id",
    )
    signature_algorithm = _canonical_signer_text(
        signature.get("algorithm"),
        field="algorithm",
    )
    if (
        expected_key_id is not None
        and signature_key_id != expected_key_id
    ):
        raise ValueError("checkpoint signer changed key_id while signing")
    if (
        expected_algorithm is not None
        and signature_algorithm != expected_algorithm
    ):
        raise ValueError("checkpoint signer changed algorithm while signing")
    signature["key_id"] = signature_key_id
    signature["algorithm"] = signature_algorithm
    return signature


def verify_authoritative_event_chain(
    events: Sequence[Mapping[str, Any] | Any],
    *,
    expected_run_id: str,
    checkpoint_store: Any,
    verifier: Any,
    trusted_latest_checkpoint: Mapping[str, Any] | None = None,
) -> LedgerVerification:
    """Verify through the persisted-checkpoint API without an import cycle."""
    from .ledger_checkpoints import verify_authoritative_event_chain as verify

    return verify(
        events,
        expected_run_id=expected_run_id,
        checkpoint_store=checkpoint_store,
        verifier=verifier,
        trusted_latest_checkpoint=trusted_latest_checkpoint,
    )


verify_event_chain_authoritatively = verify_authoritative_event_chain


__all__ = [
    "ARTIFACT_MANIFEST_ATTESTATION_SCHEMA_VERSION",
    "ARTIFACT_MANIFEST_PREDICATE_TYPE",
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "CHECKPOINT_SCHEMA_VERSION",
    "CHECKPOINT_PREDICATE_TYPE",
    "ContentAddressedArtifactStore",
    "EMPTY_ARTIFACT_MANIFEST",
    "EMPTY_ARTIFACT_MANIFEST_HASH",
    "EVENT_HASH_REDACTION_RULES_VERSION_BY_SCHEMA",
    "EVENT_HASH_SCHEMA_ALLOWED_PREDECESSORS_BY_SCHEMA",
    "EVENT_HASH_SCHEMA_VERSION",
    "EVENT_IDENTITY_CHAIN_SCOPE",
    "EVENT_IDENTITY_HEAD_SCOPE",
    "EVENT_IDENTITY_SCHEMA_VERSION",
    "EVIDENCE_COMMIT_EVENT_KIND",
    "EVIDENCE_COMMIT_EVENT_SOURCE",
    "ArtifactIntegrityError",
    "LedgerError",
    "LedgerFields",
    "LedgerVerification",
    "LEGACY_EVENT_HASH_SCHEMA_VERSION",
    "LEGACY_IMPORT_GENESIS",
    "LEGACY_RAW_PAYLOAD_COMMITMENT_SCHEMA_VERSION",
    "LEGACY_REDACTION_RULES_VERSION",
    "NATIVE_GENESIS",
    "REDACTION_RULES_BY_VERSION",
    "REDACTION_RULES_VERSION",
    "artifact_manifest_hash",
    "artifact_manifest_hash_for_payload",
    "build_artifact_manifest",
    "build_legacy_import_ledger_fields",
    "build_ledger_fields",
    "canonical_json_bytes",
    "canonical_json_text",
    "canonical_payload_hash",
    "compute_event_hash",
    "compute_event_identity_hash",
    "compute_head_event_identity_hash",
    "create_artifact_manifest_attestation",
    "create_ledger_checkpoint",
    "event_hash_schema_transition_allowed",
    "legacy_raw_payload_manifest_hash",
    "prepare_event_payload",
    "rebuild_projection",
    "sha256_hex",
    "strict_json_object_loads",
    "supported_event_hash_schema_versions",
    "verify_authoritative_event_chain",
    "verify_event_chain",
    "verify_event_chain_authoritatively",
    "verify_event_chain_structure",
    "verify_artifact_manifest_attestation",
]
