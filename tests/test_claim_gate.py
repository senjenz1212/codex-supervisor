from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import hmac
import itertools
import json
from pathlib import Path
from uuid import UUID

import pytest

from supervisor.claim_gate import (
    ClaimGate as _ClaimGate,
    ClaimLevel,
    UnsupportedClaimError,
    external_authority_attestation_payload,
    independent_verifier_attestation_payload,
)
from supervisor.evidence_ledger import LedgerVerification
from supervisor.grade_revisions import GradeBook, RunEnvelopeRef
from supervisor.task_environment import Grade, canonical_task_identity
from supervisor.trace_graph import (
    EdgeType,
    NodeType,
    TraceEdge,
    TraceGraph,
    TraceIdentity,
    TraceNode,
    canonical_revision_hash,
    trace_instance_id_from_hash,
)

_PRODUCER_PRINCIPAL_ID = "claim-gate-fixture-producer"
_VERIFIER_PRINCIPAL_ID = "claim-gate-fixture-verifier"
_VERIFIER_ATTESTATION_KEY = b"claim-gate-fixture-verifier-key"
_EXTERNAL_AUTHORITY_ID = "claim-gate-fixture-external-authority"
_EXTERNAL_AUTHORITY_KEY = b"claim-gate-fixture-external-authority-key"


class _HmacVerifierAttestor:
    def verify(self, payload: bytes, signature: dict[str, object]) -> bool:
        expected = hmac.new(
            _VERIFIER_ATTESTATION_KEY,
            payload,
            sha256,
        ).hexdigest()
        return hmac.compare_digest(
            str(signature.get("hmac_sha256") or ""),
            expected,
        )


class _HmacExternalAuthority:
    def verify(self, payload: bytes, signature: dict[str, object]) -> bool:
        expected = hmac.new(
            _EXTERNAL_AUTHORITY_KEY,
            payload,
            sha256,
        ).hexdigest()
        return hmac.compare_digest(
            str(signature.get("hmac_sha256") or ""),
            expected,
        )


_TRUSTED_VERIFIER_ATTESTORS = {
    _VERIFIER_PRINCIPAL_ID: _HmacVerifierAttestor(),
}
_TRUSTED_EXTERNAL_AUTHORITIES = {
    _EXTERNAL_AUTHORITY_ID: _HmacExternalAuthority(),
}


class _FixtureAuthority:
    def __init__(
        self,
        *,
        ledger_verifications: dict[str, LedgerVerification],
        gradebook: GradeBook,
    ) -> None:
        self.ledger_verifications = ledger_verifications
        self.gradebook = gradebook

    def __call__(
        self,
        run_id: str,
        expected_head_hash: str,
    ) -> LedgerVerification | None:
        verification = self.ledger_verifications.get(run_id)
        if (
            verification is None
            or verification.head_event_hash != expected_head_hash
        ):
            return None
        return verification


class ClaimGate(_ClaimGate):
    """Exercise ClaimGate with the fixture trust store unless overridden."""

    @staticmethod
    def _authority_defaults(kwargs):
        kwargs.setdefault(
            "trusted_verifier_attestors",
            _TRUSTED_VERIFIER_ATTESTORS,
        )
        kwargs.setdefault(
            "trusted_external_authorities",
            _TRUSTED_EXTERNAL_AUTHORITIES,
        )
        ledger_authority = kwargs.get("ledger_verification_resolver")
        if isinstance(ledger_authority, _FixtureAuthority):
            kwargs.setdefault(
                "grade_authority",
                ledger_authority.gradebook,
            )

    @classmethod
    def max_claim_level(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().max_claim_level(*args, **kwargs)

    @classmethod
    def derived_claim_flags(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().derived_claim_flags(*args, **kwargs)

    @classmethod
    def derive_report(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().derive_report(*args, **kwargs)

    @classmethod
    def govern_report(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().govern_report(*args, **kwargs)

    @classmethod
    def validate_report(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().validate_report(*args, **kwargs)

    @classmethod
    def validate_derived_report(cls, *args, **kwargs):
        cls._authority_defaults(kwargs)
        return super().validate_derived_report(*args, **kwargs)


def _write_artifact(
    evidence_root: Path,
    ref: str,
    content: bytes,
) -> dict[str, str]:
    path = evidence_root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "ref": ref,
        "sha256": sha256(content).hexdigest(),
    }


def _write_json_artifact(
    evidence_root: Path,
    ref: str,
    value: object,
) -> dict[str, str]:
    return _write_artifact(
        evidence_root,
        ref,
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _task_identity(task_id: str) -> dict[str, str]:
    return {
        "repo": "https://github.com/example/repository.git",
        "canonical_repo_id": (
            "https://github.com/example/repository.git"
        ),
        "revision": "1" * 40,
        "dataset_hash": "2" * 64,
        "split_hash": "3" * 64,
        "canonical_task_key": task_id,
    }


def _externally_attested_descriptor(
    descriptor: dict[str, str],
    *,
    evidence_kind: str,
) -> dict[str, object]:
    payload = external_authority_attestation_payload(
        evidence_kind=evidence_kind,
        authority_id=_EXTERNAL_AUTHORITY_ID,
        reference=descriptor["ref"],
        sha256_digest=descriptor["sha256"],
    )
    return {
        **descriptor,
        "attestation": {
            "authority_id": _EXTERNAL_AUTHORITY_ID,
            "signature": {
                "hmac_sha256": hmac.new(
                    _EXTERNAL_AUTHORITY_KEY,
                    payload,
                    sha256,
                ).hexdigest(),
            },
        },
    }


def _attested_hidden_verifier(
    result: dict[str, str],
    *,
    producer_principal_id: str = _PRODUCER_PRINCIPAL_ID,
    verifier_principal_id: str = _VERIFIER_PRINCIPAL_ID,
) -> dict[str, object]:
    descriptor: dict[str, object] = {
        "verifier_id": "hidden-verifier/v1",
        "producer_principal_id": producer_principal_id,
        "independent": True,
        "hidden": True,
        "result_ref": result["ref"],
        "result_sha256": result["sha256"],
    }
    signed_payload = independent_verifier_attestation_payload(
        verifier_id=str(descriptor["verifier_id"]),
        producer_principal_id=producer_principal_id,
        verifier_principal_id=verifier_principal_id,
        result_ref=result["ref"],
        result_sha256=result["sha256"],
    )
    descriptor["attestation"] = {
        "verifier_principal_id": verifier_principal_id,
        "signature": {
            "hmac_sha256": hmac.new(
                _VERIFIER_ATTESTATION_KEY,
                signed_payload,
                sha256,
            ).hexdigest(),
        },
    }
    return descriptor


def _claim_gate_kwargs(
    ledger_resolver: Callable[[str, str], LedgerVerification | None] | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "trusted_verifier_attestors": _TRUSTED_VERIFIER_ATTESTORS,
        "trusted_external_authorities": _TRUSTED_EXTERNAL_AUTHORITIES,
    }
    if ledger_resolver is not None:
        kwargs["ledger_verification_resolver"] = ledger_resolver
    if isinstance(ledger_resolver, _FixtureAuthority):
        kwargs["grade_authority"] = ledger_resolver.gradebook
    return kwargs


def _artifact_ref(namespace: str, filename: str) -> str:
    clean_namespace = namespace.strip("/")
    if not clean_namespace:
        return f"artifacts/{filename}"
    return f"artifacts/{clean_namespace}/{filename}"


def _closed_trace_document(
    *,
    experiment_id: str,
    assignments: list[dict[str, object]],
    grades: list[dict[str, object]],
    verifier_id: str,
    verifier_implementation_hash: str,
) -> dict[str, object]:
    instance_number = 0

    def node(
        node_type: NodeType,
        logical_id: str,
        *,
        revision_hash: str | None = None,
        **kwargs: object,
    ) -> TraceNode:
        nonlocal instance_number
        instance_number += 1
        exact_revision_hash = revision_hash or canonical_revision_hash(
            {
                "node_type": node_type.value,
                "logical_id": logical_id,
                "experiment_id": experiment_id,
            }
        )
        return TraceNode(
            identity=TraceIdentity(
                namespace="claim-gate-fixture",
                node_type=node_type,
                logical_id=logical_id,
                revision_hash=exact_revision_hash,
                instance_id=trace_instance_id_from_hash(
                    timestamp_ms=1_720_000_000_000 + instance_number,
                    content_hash=canonical_revision_hash(
                        {
                            "logical_id": logical_id,
                            "instance_number": instance_number,
                        }
                    ),
                    domain="claim-gate-test-trace",
                ),
            ),
            **kwargs,
        )

    objective = node(NodeType.OBJ, "OBJ-CAUSAL-OUTCOME")
    requirement = node(NodeType.REQ, "REQ-CAUSAL-OUTCOME")
    test = node(NodeType.TEST, "TEST-CAUSAL-OUTCOME")
    analysis = node(
        NodeType.ANL,
        "ANL-B-VS-C",
        attributes={
            "experiment_id": experiment_id,
            "comparison": "B_vs_C",
        },
    )
    decision = node(
        NodeType.DEC,
        "DEC-B-VS-C-CLAIM-AUTHORITY",
        attributes={
            "experiment_id": experiment_id,
            "grade_citations": [
                {
                    "grade_id": grade_record["grade"]["grade_id"],
                    "revision_hash": (
                        grade_record["grade"]["revision_hash"]
                    ),
                    "acknowledged_invalidation_hashes": [],
                }
                for grade_record in grades
            ],
        },
    )
    promotion = node(
        NodeType.PROMOTION,
        "PROMOTION-B-VS-C-L3",
        attributes={"experiment_id": experiment_id},
    )
    nodes = [objective, requirement, test, analysis, decision, promotion]
    edges = [
        TraceEdge(
            requirement.identity,
            EdgeType.IMPLEMENTS,
            objective.identity,
        ),
        TraceEdge(test.identity, EdgeType.TESTS, requirement.identity),
        TraceEdge(decision.identity, EdgeType.DERIVED_FROM, analysis.identity),
        TraceEdge(promotion.identity, EdgeType.PROMOTES, decision.identity),
    ]
    assignments_by_task = {
        str(assignment["task_id"]): assignment
        for assignment in assignments
    }
    assignment_nodes: dict[str, TraceNode] = {}
    for task_id, assignment in assignments_by_task.items():
        assignment_node = node(
            NodeType.ASN,
            f"ASN-{task_id}",
            attributes={
                "task_id": task_id,
                "canonical_task_id": assignment["canonical_task_id"],
                "assignment_id": assignment["assignment_id"],
            },
        )
        assignment_nodes[task_id] = assignment_node
        nodes.append(assignment_node)
        edges.append(
            TraceEdge(
                assignment_node.identity,
                EdgeType.SUPPORTS,
                test.identity,
            )
        )

    for grade_record in grades:
        task_id = str(grade_record["task_id"])
        arm = str(grade_record["arm"])
        grade = grade_record["grade"]
        assert isinstance(grade, dict)
        run_envelope = grade["run_envelope"]
        assert isinstance(run_envelope, dict)
        run_id = str(run_envelope["run_id"])
        arm_token = "b" if arm == "supervisor" else "c"
        run = node(
            NodeType.RUN,
            f"RUN-{task_id}-{arm_token}",
            pinned=True,
            attributes={
                "task_id": task_id,
                "arm": arm,
                "run_id": run_id,
            },
        )
        artifact = node(
            NodeType.ART,
            f"ART-{task_id}-{arm_token}",
            runtime_evidence=True,
            attributes={
                "task_id": task_id,
                "arm": arm,
                "run_id": run_id,
                "frozen_result_hash": run_envelope["frozen_result_hash"],
            },
        )
        grade_node = node(
            NodeType.GRADE,
            f"GRADE-{task_id}-{arm_token}",
            revision_hash=str(grade["revision_hash"]),
            verifier_id=verifier_id,
            verifier_revision_hash=verifier_implementation_hash,
            attributes={
                "task_id": task_id,
                "arm": arm,
                "grade_id": grade["grade_id"],
                "grade_revision_hash": grade["revision_hash"],
            },
        )
        nodes.extend((run, artifact, grade_node))
        edges.extend(
            (
                TraceEdge(
                    run.identity,
                    EdgeType.ASSIGNED_BY,
                    assignment_nodes[task_id].identity,
                ),
                TraceEdge(
                    artifact.identity,
                    EdgeType.DERIVED_FROM,
                    run.identity,
                ),
                TraceEdge(
                    grade_node.identity,
                    EdgeType.EVALUATES,
                    artifact.identity,
                ),
                TraceEdge(
                    analysis.identity,
                    EdgeType.DERIVED_FROM,
                    grade_node.identity,
                ),
            )
        )
    return TraceGraph(nodes=nodes, edges=edges).to_dict()


def _write_pilot_confirmation_lineage(
    evidence_root: Path,
    *,
    experiment_id: str,
    powered_design: dict[str, object],
    confirmation_task_ids: list[str],
    artifact_namespace: str = "",
) -> dict[str, str]:
    pilot_id = f"{experiment_id}-pilot"
    pilot_task_prefix = (
        f"{artifact_namespace}-pilot-task"
        if artifact_namespace
        else "pilot-task"
    )
    pilot_protocol = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "pilot-protocol.json"),
        {
            "schema_version": "supervisor-experiment-protocol/v1",
            "phase": "pilot",
            "experiment_id": pilot_id,
            "comparison": "B_vs_C",
            "registered_at_ms": 100,
        },
    )
    pilot_task_ids = [
        f"{pilot_task_prefix}-{index:02d}"
        for index in range(10)
    ]
    pilot_tasks = [
        {
            "task_id": task_id,
            "task_identity": _task_identity(task_id),
            "canonical_task_id": canonical_task_identity(
                _task_identity(task_id)
            ),
        }
        for task_id in pilot_task_ids
    ]
    pilot_roster = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "pilot-roster.json"),
        {
            "schema_version": "supervisor-experiment-roster/v1",
            "phase": "pilot",
            "experiment_id": pilot_id,
            "protocol_sha256": pilot_protocol["sha256"],
            "frozen_at_ms": 200,
            "tasks": pilot_tasks,
        },
    )
    pilot_assignment_records = [
        {
            "task_id": task_id,
            "canonical_task_id": canonical_task_identity(
                _task_identity(task_id)
            ),
            "assignment_id": _sha256_text(
                f"pilot-assignment:{pilot_id}:{task_id}"
            ),
        }
        for task_id in pilot_task_ids
    ]
    pilot_assignments = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "pilot-assignments.json"),
        {
            "schema_version": "supervisor-pilot-assignments/v1",
            "experiment_id": pilot_id,
            "roster_sha256": pilot_roster["sha256"],
            "frozen_at_ms": 220,
            "assignments": pilot_assignment_records,
        },
    )
    pilot_outcomes: list[dict[str, object]] = []
    pilot_rows: list[dict[str, object]] = []
    for index, assignment in enumerate(pilot_assignment_records):
        task_id = str(assignment["task_id"])
        b_pass = index < 9
        c_pass = not b_pass
        for arm, passed in (
            ("supervisor", b_pass),
            ("compute_matched_direct", c_pass),
        ):
            pilot_outcomes.append(
                {
                    "task_id": task_id,
                    "canonical_task_id": assignment[
                        "canonical_task_id"
                    ],
                    "assignment_id": assignment["assignment_id"],
                    "arm": arm,
                    "status": "completed",
                    "passed": passed,
                }
            )
        pilot_rows.append(
            {
                "task_id": task_id,
                "task_identity": _task_identity(task_id),
                "canonical_task_id": assignment[
                    "canonical_task_id"
                ],
                "assignment_id": assignment["assignment_id"],
                "b_pass": b_pass,
                "c_pass": c_pass,
            }
        )
    pilot_terminal_outcomes = _write_json_artifact(
        evidence_root,
        _artifact_ref(
            artifact_namespace,
            "pilot-terminal-outcomes.json",
        ),
        {
            "schema_version": (
                "supervisor-pilot-terminal-outcomes/v1"
            ),
            "experiment_id": pilot_id,
            "assignments_sha256": pilot_assignments["sha256"],
            "frozen_at_ms": 280,
            "outcomes": pilot_outcomes,
        },
    )
    pilot_analysis = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "pilot-analysis.json"),
        {
            "schema_version": "supervisor-pilot-analysis/v1",
            "experiment_id": pilot_id,
            "protocol_sha256": pilot_protocol["sha256"],
            "roster_sha256": pilot_roster["sha256"],
            "assignments_sha256": pilot_assignments["sha256"],
            "terminal_outcomes_sha256": (
                pilot_terminal_outcomes["sha256"]
            ),
            "completed_at_ms": 300,
            "task_rows": pilot_rows,
            "estimates": {
                "alternative_b_win_rate": powered_design["power"][
                    "alternative_b_win_rate"
                ],
                "expected_discordance_rate": powered_design["power"][
                    "expected_discordance_rate"
                ],
            },
        },
    )
    confirmation_protocol = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "confirmation-protocol.json"),
        {
            "schema_version": "supervisor-experiment-protocol/v1",
            "phase": "confirmation",
            "experiment_id": experiment_id,
            "comparison": "B_vs_C",
            "registered_at_ms": 400,
            "powered_design_sha256": _sha256_json(powered_design),
            "target_power": powered_design["power"]["target_power"],
            "pilot_protocol_sha256": pilot_protocol["sha256"],
            "pilot_roster_sha256": pilot_roster["sha256"],
            "pilot_analysis_sha256": pilot_analysis["sha256"],
        },
    )
    confirmation_roster = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "confirmation-roster.json"),
        {
            "schema_version": "supervisor-experiment-roster/v1",
            "phase": "confirmation",
            "experiment_id": experiment_id,
            "protocol_sha256": confirmation_protocol["sha256"],
            "frozen_at_ms": 500,
            "tasks": [
                {
                    "task_id": task_id,
                    "task_identity": _task_identity(task_id),
                    "canonical_task_id": canonical_task_identity(
                        _task_identity(task_id)
                    ),
                }
                for task_id in confirmation_task_ids
            ],
        },
    )
    return _write_json_artifact(
        evidence_root,
        _artifact_ref(
            artifact_namespace,
            "pilot-confirmation-lineage.json",
        ),
        {
            "schema_version": "supervisor-pilot-confirmation-lineage/v1",
            "pilot": {
                "experiment_id": pilot_id,
                "protocol": pilot_protocol,
                "roster": pilot_roster,
                "assignments": pilot_assignments,
                "terminal_outcomes": pilot_terminal_outcomes,
                "analysis": pilot_analysis,
            },
            "confirmation": {
                "experiment_id": experiment_id,
                "protocol": confirmation_protocol,
                "roster": confirmation_roster,
            },
            "derivation": {
                "kind": "pilot_informs_confirmation",
                "pilot_analysis_sha256": pilot_analysis["sha256"],
                "confirmation_protocol_sha256": (
                    confirmation_protocol["sha256"]
                ),
            },
        },
    )


def _fixture_replay_bundle(evidence_root: Path) -> dict[str, object]:
    run_manifest = _write_artifact(
        evidence_root,
        "artifacts/run-manifest.json",
        b'{"run_id":"fixture-replay"}\n',
    )
    artifact_manifest = _write_artifact(
        evidence_root,
        "artifacts/artifact-manifest.json",
        b'{"artifacts":["fixture-replay.json"]}\n',
    )
    replay = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay.json",
        b'{"status":"passed"}\n',
    )
    trace = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay-trace.jsonl",
        b'{"event":"completed"}\n',
    )
    return {
        "pins": {
            "repository": "example/repository@abc123",
            "dataset": "fixture-replay@v1",
        },
        "hashes": {
            "run_manifest_sha256": run_manifest["sha256"],
            "artifact_manifest_sha256": artifact_manifest["sha256"],
        },
        "artifacts": [run_manifest, artifact_manifest, replay],
        "traceable_detector": {
            "detector_id": "fixture-replay-detector/v1",
            "trace_ref": trace["ref"],
            "trace_sha256": trace["sha256"],
        },
    }


def _outcome_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _fixture_replay_bundle(evidence_root)
    result = _write_artifact(
        evidence_root,
        "artifacts/hidden-verifier-result.json",
        b'{"verdict":"passed"}\n',
    )
    bundle["independent_hidden_verifier"] = _attested_hidden_verifier(result)
    return bundle


def _causal_bundle(
    evidence_root: Path,
    *,
    task_count: int = 12,
    target_power: float = 0.80,
    required_discordant_pairs: int = 12,
    closed_trace: bool = False,
    ledger_verifications: dict[str, LedgerVerification] | None = None,
    study_lineage: bool = False,
    experiment_id: str = "claim-gate-causal-fixture",
    artifact_namespace: str = "",
    task_prefix: str = "causal-task",
    replication_context: dict[str, object] | None = None,
    gradebook: GradeBook | None = None,
) -> dict[str, object]:
    bundle = _outcome_bundle(evidence_root)
    grade_authority = gradebook or GradeBook(":memory:")
    assignment_version = "fixture-v1"
    verifier_id = "hidden-verifier/v1"
    verifier_version = "1.0"
    verifier_config_hash = _sha256_text("hidden-verifier-config-v1")
    verifier_implementation_hash = _sha256_text(
        "hidden-verifier-implementation-v1"
    )
    assignment_hmac_key = sha256(
        b"claim-gate-fixture-assignment-key"
    ).digest()
    powered_design = {
        "paired": True,
        "assignment_unit": "task",
        "analysis_unit": "task",
        "randomization_method": "hmac-sha256",
        "power": {
            "method": "exact_mcnemar",
            "alpha": 0.05,
            "target_power": target_power,
            "alternative_b_win_rate": 0.90,
            "expected_discordance_rate": 1.0,
            "required_discordant_pairs": required_discordant_pairs,
            "required_task_count": required_discordant_pairs,
        },
    }
    powered_design_sha256 = _sha256_json(powered_design)
    arm_orders = tuple(
        itertools.permutations(
            (
                "production_baseline",
                "supervisor",
                "compute_matched_direct",
            )
        )
    )
    assignments: list[dict[str, object]] = []
    grades: list[dict[str, object]] = []
    frozen_results: list[dict[str, object]] = []
    task_rows: list[dict[str, object]] = []
    trace_nodes: list[dict[str, object]] = []
    ledger_runs: list[dict[str, object]] = []
    ledger_event_payloads: list[dict[str, object]] = []
    for index in range(task_count):
        task_id = f"{task_prefix}-{index:02d}"
        task_identity = _task_identity(task_id)
        canonical_task_id = canonical_task_identity(task_identity)
        block = {
            "model": "fixture-model",
            "powered_design_sha256": powered_design_sha256,
            "repo": "example/repository",
            "task_family": "claim-gate",
        }
        assignment_message = "||".join(
            (
                experiment_id,
                canonical_task_id,
                assignment_version,
                json.dumps(block, sort_keys=True, separators=(",", ":")),
            )
        )
        assignment_id = hmac.new(
            assignment_hmac_key,
            assignment_message.encode("utf-8"),
            sha256,
        ).hexdigest()
        order = arm_orders[int(assignment_id[:16], 16) % len(arm_orders)]
        assigned_at_ms = 1_000 + index * 10
        persisted_at_ms = assigned_at_ms + 1
        first_execution_started_at_ms = persisted_at_ms + 1
        assignments.append(
            {
                "task_id": task_id,
                "task_identity": task_identity,
                "canonical_task_id": canonical_task_id,
                "assignment_id": assignment_id,
                "block": block,
                "assignment_message_sha256": _sha256_text(
                    assignment_message
                ),
                "order": list(order),
                "assigned_at_ms": assigned_at_ms,
                "persisted_at_ms": persisted_at_ms,
                "first_execution_started_at_ms": (
                    first_execution_started_at_ms
                ),
            }
        )
        trace_nodes.append(
            {
                "identity": {
                    "namespace": "harness-v1",
                    "node_type": "ASN",
                    "logical_id": f"ASN-{task_id}",
                    "revision_hash": _sha256_text(
                        f"assignment-node:{assignment_id}"
                    ),
                    "instance_id": str(UUID(int=index + 1)),
                },
                "attributes": {
                    "task_id": task_id,
                    "assignment_id": assignment_id,
                },
            }
        )

        row: dict[str, object] = {
            "task_id": task_id,
            "canonical_task_id": canonical_task_id,
            "assignment_id": assignment_id,
        }
        for arm_index, (arm, passed) in enumerate(
            (
                ("supervisor", True),
                ("compute_matched_direct", False),
            )
        ):
            arm_key = "b" if arm == "supervisor" else "c"
            run_id = f"run-{task_id}-{arm_key}"
            run_envelope = {
                "run_id": run_id,
                "run_envelope_hash": _sha256_text(
                    f"run-envelope:{task_id}:{arm}"
                ),
                "frozen_result_hash": _sha256_text(
                    f"frozen-result:{task_id}:{arm}"
                ),
            }
            grade_revision = grade_authority.append_grade(
                run=RunEnvelopeRef(**run_envelope),
                grade=Grade(
                    verifier_id=verifier_id,
                    verifier_version=verifier_version,
                    verifier_hash=verifier_implementation_hash,
                    frozen_result_hash=str(
                        run_envelope["frozen_result_hash"]
                    ),
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    evidence={"fixture": "hidden-verifier"},
                    failure_classification=(
                        "" if passed else "verified_failure"
                    ),
                ),
                verifier_config_hash=verifier_config_hash,
                verifier_implementation_hash=(
                    verifier_implementation_hash
                ),
            )
            grade = grade_revision.to_dict()
            grade_id = grade_revision.grade_id
            grades.append(
                {
                    "task_id": task_id,
                    "arm": arm,
                    "grade": grade,
                }
            )
            row[f"{arm_key}_grade_id"] = grade_id
            row[f"{arm_key}_grade_revision_hash"] = grade["revision_hash"]
            row[f"{arm_key}_pass"] = passed
            frozen_results.append(
                {
                    "task_id": task_id,
                    "canonical_task_id": canonical_task_id,
                    "assignment_id": assignment_id,
                    "arm": arm,
                    "run_id": run_id,
                    "status": "completed",
                    "frozen_result_hash": run_envelope[
                        "frozen_result_hash"
                    ],
                    "passed": passed,
                }
            )
            trace_nodes.append(
                {
                    "identity": {
                        "namespace": "harness-v1",
                        "node_type": "GRADE",
                        "logical_id": f"GRADE-{task_id}-{arm_key}",
                        "revision_hash": grade["revision_hash"],
                        "instance_id": str(
                            UUID(int=100 + index * 2 + arm_index)
                        ),
                    },
                    "verifier_id": verifier_id,
                    "verifier_revision_hash": (
                        verifier_implementation_hash
                    ),
                    "attributes": {
                        "task_id": task_id,
                        "arm": arm,
                        "grade_id": grade_id,
                        "grade_revision_hash": grade["revision_hash"],
                    },
                }
            )
            event_id = f"event-{task_id}-{arm_key}-3"
            event_payload = _write_json_artifact(
                evidence_root,
                _artifact_ref(
                    artifact_namespace,
                    f"ledger-head-{index:02d}-{arm_key}.json",
                ),
                {
                    "schema_version": (
                        "supervisor-experiment-ledger-event/v1"
                    ),
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "canonical_task_id": canonical_task_id,
                    "assignment_id": assignment_id,
                    "arm": arm,
                    "frozen_result_hash": run_envelope[
                        "frozen_result_hash"
                    ],
                    "grade_id": grade_id,
                    "grade_revision_hash": grade["revision_hash"],
                },
            )
            event_descriptor = {
                "run_id": run_id,
                "event_id": event_id,
                **event_payload,
            }
            ledger_event_payloads.append(event_descriptor)
            head_event_hash = event_payload["sha256"]
            verification = LedgerVerification(
                valid=True,
                run_id=run_id,
                event_count=3,
                head_event_id=event_id,
                head_event_hash=head_event_hash,
                expected_head_hash=head_event_hash,
                truncation_checked=True,
                authoritative_head_verified=True,
                external_anchor_ref=(
                    "file:///immutable-ledger-checkpoints/"
                    f"{run_id}.json"
                ),
            )
            if ledger_verifications is not None:
                ledger_verifications[run_id] = verification
            ledger_runs.append(
                {
                    "run_id": run_id,
                    "expected_head_hash": head_event_hash,
                    "verification": verification.to_dict(),
                    "head_event_payload": event_descriptor,
                }
            )
        task_rows.append(row)

    assignment_artifact = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-assignments.json"),
        {
            "schema_version": "supervisor-experiment-assignments/v1",
            "experiment_id": experiment_id,
            "assignment_version": assignment_version,
            "powered_design_sha256": powered_design_sha256,
            "randomization": {
                "method": "hmac-sha256",
                "assignment_unit": "task",
                "key_commitment_sha256": sha256(
                    assignment_hmac_key
                ).hexdigest(),
                "hmac_key_hex": assignment_hmac_key.hex(),
            },
            "assignments": assignments,
        },
    )
    grade_artifact = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-grades.json"),
        {
            "schema_version": "supervisor-grade-revision-set/v1",
            "experiment_id": experiment_id,
            "grades": grades,
        },
    )
    trace_document = (
        _closed_trace_document(
            experiment_id=experiment_id,
            assignments=assignments,
            grades=grades,
            verifier_id=verifier_id,
            verifier_implementation_hash=(
                verifier_implementation_hash
            ),
        )
        if closed_trace
        else {
            "schema_version": "supervisor-trace-graph/v1",
            "edge_direction": "source_record_to_prerequisite",
            "nodes": trace_nodes,
            "edges": [],
            "waivers": [],
        }
    )
    trace_artifact = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-trace.json"),
        trace_document,
    )
    ledger_artifact = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-ledger.json"),
        {
            "schema_version": "supervisor-ledger-verifications/v2",
            "experiment_id": experiment_id,
            "assignment_artifact_sha256": assignment_artifact["sha256"],
            "grade_artifact_sha256": grade_artifact["sha256"],
            "runs": ledger_runs,
        },
    )
    verifier_artifact = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-verifier.json"),
        {
            "schema_version": "supervisor-verifier-manifest/v1",
            "verifier_id": verifier_id,
            "verifier_version": verifier_version,
            "verifier_config_hash": verifier_config_hash,
            "verifier_implementation_hash": verifier_implementation_hash,
            "independent": True,
            "hidden": True,
        },
    )
    analysis_lineage: dict[str, object] = {
        "assignments": assignment_artifact,
        "grades": grade_artifact,
        "trace": trace_artifact,
        "ledger": ledger_artifact,
        "verifier": verifier_artifact,
    }
    if study_lineage:
        analysis_lineage["study"] = _write_pilot_confirmation_lineage(
            evidence_root,
            experiment_id=experiment_id,
            powered_design=powered_design,
            confirmation_task_ids=[
                str(assignment["task_id"])
                for assignment in assignments
            ],
            artifact_namespace=artifact_namespace,
        )
    analysis_document: dict[str, object] = {
        "schema_version": "supervisor-b-vs-c-analysis/v1",
        "experiment_id": experiment_id,
        "comparison": "B_vs_C",
        "design": powered_design,
        "task_rows": task_rows,
        "result": {
            "task_count": task_count,
            "n11": 0,
            "n10": task_count,
            "n01": 0,
            "n00": 0,
            "discordant_pairs": task_count,
            "effect": {
                "metric": "paired_risk_difference",
                "estimate": 1.0,
                "direction": "B_over_C",
            },
            "test": {
                "method": "exact_mcnemar_two_sided",
                "alpha": 0.05,
                "p_value": 2 ** (1 - task_count),
                "reject_null": True,
            },
        },
        "lineage": analysis_lineage,
    }
    if replication_context is not None:
        analysis_document["replication_context"] = replication_context
    analysis = _write_json_artifact(
        evidence_root,
        _artifact_ref(artifact_namespace, "b-vs-c-analysis.json"),
        analysis_document,
    )
    causal_evidence: dict[str, object] = {
        "comparison": "B_vs_C",
        "randomized": True,
        "powered": True,
        "supports_improvement": True,
        "analysis_ref": analysis["ref"],
        "analysis_sha256": analysis["sha256"],
    }
    if study_lineage:
        decision_nodes = [
            node
            for node in trace_document["nodes"]
            if node["identity"]["node_type"] == "DEC"
        ]
        assert len(decision_nodes) == 1
        decision_identity = decision_nodes[0]["identity"]
        manifest = _write_json_artifact(
            evidence_root,
            _artifact_ref(
                artifact_namespace,
                "experiment-evidence-manifest.json",
            ),
            {
                "schema_version": (
                    "supervisor-experiment-evidence-manifest/v1"
                ),
                "experiment_id": experiment_id,
                "artifacts": {
                    "analysis": analysis,
                    **analysis_lineage,
                },
                "analysis_hashes": {
                    "analysis_sha256": analysis["sha256"],
                    "design_sha256": _sha256_json(powered_design),
                    "task_rows_sha256": _sha256_json(task_rows),
                    "result_sha256": _sha256_json(
                        analysis_document["result"]
                    ),
                },
                "frozen_roster": [
                    {
                        "task_id": assignment["task_id"],
                        "task_identity": assignment["task_identity"],
                        "canonical_task_id": assignment[
                            "canonical_task_id"
                        ],
                    }
                    for assignment in assignments
                ],
                "frozen_results": frozen_results,
                "current_grade_revisions": [
                    {
                        "task_id": grade_record["task_id"],
                        "arm": grade_record["arm"],
                        "grade_id": grade_record["grade"]["grade_id"],
                        "revision_hash": grade_record["grade"][
                            "revision_hash"
                        ],
                    }
                    for grade_record in grades
                ],
                "verifier_implementation": {
                    "verifier_id": verifier_id,
                    "verifier_version": verifier_version,
                    "verifier_config_hash": verifier_config_hash,
                    "verifier_implementation_hash": (
                        verifier_implementation_hash
                    ),
                },
                "trace_decision": {
                    "canonical_key": decision_identity["canonical_key"],
                    "revision_hash": decision_identity["revision_hash"],
                },
                "ledger_event_payloads": ledger_event_payloads,
            },
        )
        causal_evidence["experiment_manifest"] = (
            _externally_attested_descriptor(
                manifest,
                evidence_kind="experiment_evidence_manifest",
            )
        )
    bundle["randomized_powered_b_vs_c"] = causal_evidence
    return bundle


def _authoritative_causal_bundle(
    evidence_root: Path,
    *,
    ledger_verifications: dict[str, LedgerVerification] | None = None,
    gradebook: GradeBook | None = None,
    experiment_id: str = "claim-gate-causal-fixture",
    artifact_namespace: str = "",
    task_prefix: str = "causal-task",
    replication_context: dict[str, object] | None = None,
) -> tuple[
    dict[str, object],
    _FixtureAuthority,
]:
    authoritative_verifications = (
        ledger_verifications
        if ledger_verifications is not None
        else {}
    )
    grade_authority = gradebook or GradeBook(":memory:")
    bundle = _causal_bundle(
        evidence_root,
        task_count=15,
        target_power=0.90,
        required_discordant_pairs=15,
        closed_trace=True,
        ledger_verifications=authoritative_verifications,
        study_lineage=True,
        experiment_id=experiment_id,
        artifact_namespace=artifact_namespace,
        task_prefix=task_prefix,
        replication_context=replication_context,
        gradebook=grade_authority,
    )
    return bundle, _FixtureAuthority(
        ledger_verifications=authoritative_verifications,
        gradebook=grade_authority,
    )


def _portable_authoritative_bundle(
    evidence_root: Path,
) -> tuple[
    dict[str, object],
    _FixtureAuthority,
]:
    ledger_verifications: dict[str, LedgerVerification] = {}
    gradebook = GradeBook(":memory:")
    bundle, ledger_resolver = _authoritative_causal_bundle(
        evidence_root,
        ledger_verifications=ledger_verifications,
        gradebook=gradebook,
    )
    causal_evidence = bundle["randomized_powered_b_vs_c"]
    assert isinstance(causal_evidence, dict)
    study_contexts = (
        {
            "stratum": "python",
            "model_family": "anthropic",
            "model_version": "claude-fixture@1",
            "seen_by_optimizer": True,
        },
        {
            "stratum": "unity",
            "model_family": "openai",
            "model_version": "codex-fixture@1",
            "seen_by_optimizer": True,
        },
        {
            "stratum": "typescript",
            "model_family": "google",
            "model_version": "gemini-fixture@1",
            "seen_by_optimizer": False,
        },
    )
    studies: list[dict[str, object]] = []
    for index, context in enumerate(study_contexts, start=1):
        namespace = f"replication-{index}"
        study_bundle, _ = _authoritative_causal_bundle(
            evidence_root,
            ledger_verifications=ledger_verifications,
            gradebook=gradebook,
            experiment_id=f"claim-gate-replication-{index}",
            artifact_namespace=namespace,
            task_prefix=f"replication-{index}-task",
            replication_context=dict(context),
        )
        study_evidence = study_bundle["randomized_powered_b_vs_c"]
        assert isinstance(study_evidence, dict)
        studies.append(
            {
                "analysis_ref": study_evidence["analysis_ref"],
                "analysis_sha256": study_evidence["analysis_sha256"],
                "experiment_manifest": study_evidence[
                    "experiment_manifest"
                ],
            }
        )
    replication = _write_json_artifact(
        evidence_root,
        "artifacts/strata-replication.json",
        {
            "schema_version": (
                "supervisor-strata-replication-analysis/v1"
            ),
            "source_causal_analysis_sha256": (
                causal_evidence["analysis_sha256"]
            ),
            "studies": studies,
            "result": {
                "replicated": True,
                "study_count": 3,
                "strata": ["python", "typescript", "unity"],
                "model_families": [
                    {
                        "family": "anthropic",
                        "pinned": True,
                        "seen_by_optimizer": True,
                    },
                    {
                        "family": "openai",
                        "pinned": True,
                        "seen_by_optimizer": True,
                    },
                    {
                        "family": "google",
                        "pinned": True,
                        "seen_by_optimizer": False,
                    },
                ],
            },
        },
    )
    bundle["strata_replication"] = {
        "replicated": True,
        "strata": ["python", "typescript", "unity"],
        "model_families": [
            {
                "family": "anthropic",
                "pinned": True,
                "seen_by_optimizer": True,
            },
            {
                "family": "openai",
                "pinned": True,
                "seen_by_optimizer": True,
            },
            {
                "family": "google",
                "pinned": True,
                "seen_by_optimizer": False,
            },
        ],
        "analysis_ref": replication["ref"],
        "analysis_sha256": replication["sha256"],
    }
    return bundle, ledger_resolver


def _roi_authoritative_bundle(
    evidence_root: Path,
    *,
    cheaper_harness: bool = False,
) -> tuple[
    dict[str, object],
    _FixtureAuthority,
]:
    bundle, ledger_resolver = _portable_authoritative_bundle(evidence_root)
    causal_evidence = bundle["randomized_powered_b_vs_c"]
    assert isinstance(causal_evidence, dict)
    if cheaper_harness:
        cost_components = {
            "compute": {
                "method": "token_usage_pricing",
                "baseline": {
                    "input_tokens": 2_000_000,
                    "output_tokens": 500_000,
                    "input_usd_per_million": 4.0,
                    "output_usd_per_million": 8.0,
                    "reported_cost_usd": 12.0,
                },
                "supervisor": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 0,
                    "input_usd_per_million": 4.0,
                    "output_usd_per_million": 8.0,
                    "reported_cost_usd": 4.0,
                },
            },
            "latency": {
                "method": "elapsed_seconds_value",
                "baseline": {
                    "elapsed_seconds": 7_200.0,
                    "usd_per_hour": 5.0,
                    "reported_cost_usd": 10.0,
                },
                "supervisor": {
                    "elapsed_seconds": 3_600.0,
                    "usd_per_hour": 5.0,
                    "reported_cost_usd": 5.0,
                },
            },
            "risk": {
                "method": "expected_loss",
                "baseline": {
                    "probability": 0.10,
                    "impact_usd": 30.0,
                    "reported_cost_usd": 3.0,
                },
                "supervisor": {
                    "probability": 0.10,
                    "impact_usd": 10.0,
                    "reported_cost_usd": 1.0,
                },
            },
        }
        baseline_cost = 25.0
        supervisor_cost = 10.0
    else:
        cost_components = {
            "compute": {
                "method": "token_usage_pricing",
                "baseline": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 0,
                    "input_usd_per_million": 4.0,
                    "output_usd_per_million": 8.0,
                    "reported_cost_usd": 4.0,
                },
                "supervisor": {
                    "input_tokens": 2_000_000,
                    "output_tokens": 500_000,
                    "input_usd_per_million": 4.0,
                    "output_usd_per_million": 8.0,
                    "reported_cost_usd": 12.0,
                },
            },
            "latency": {
                "method": "elapsed_seconds_value",
                "baseline": {
                    "elapsed_seconds": 3_600.0,
                    "usd_per_hour": 5.0,
                    "reported_cost_usd": 5.0,
                },
                "supervisor": {
                    "elapsed_seconds": 7_200.0,
                    "usd_per_hour": 5.0,
                    "reported_cost_usd": 10.0,
                },
            },
            "risk": {
                "method": "expected_loss",
                "baseline": {
                    "probability": 0.10,
                    "impact_usd": 10.0,
                    "reported_cost_usd": 1.0,
                },
                "supervisor": {
                    "probability": 0.10,
                    "impact_usd": 30.0,
                    "reported_cost_usd": 3.0,
                },
            },
        }
        baseline_cost = 10.0
        supervisor_cost = 25.0
    incremental_cost = supervisor_cost - baseline_cost
    cost_per_incremental_success = incremental_cost / 15
    incremental_value = 30.0
    net_value = incremental_value - incremental_cost
    business_value_protocol = _write_json_artifact(
        evidence_root,
        "artifacts/business-value-protocol.json",
        {
            "schema_version": "supervisor-business-value-protocol/v1",
            "protocol_id": "business-value-fixture-v1",
            "experiment_id": "claim-gate-causal-fixture",
            "registered_at_ms": 600,
            "metric": "independent_hidden_verifier_pass",
            "currency": "USD",
            "value_per_success_usd": 2.0,
            "decision_rule": "net_value_gt_zero",
            "decision_horizon_task_count": 15,
            "frozen": True,
            "valuation_basis": {
                "kind": "approved_business_case",
                "owner": "Finance Operations",
                "reference": "business-case://harness-v1/fixture",
            },
        },
    )
    cost_provenance = _write_json_artifact(
        evidence_root,
        "artifacts/incremental-cost-provenance.json",
        {
            "schema_version": (
                "supervisor-incremental-cost-provenance/v1"
            ),
            "measurement_id": "roi-fixture-v1",
            "experiment_id": "claim-gate-causal-fixture",
            "causal_analysis_sha256": causal_evidence["analysis_sha256"],
            "business_value_protocol_sha256": (
                business_value_protocol["sha256"]
            ),
            "measurement_started_at_ms": 1_002,
            "measurement_completed_at_ms": 2_000,
            "task_count": 15,
            "components": cost_components,
            "totals": {
                "baseline_cost_usd": baseline_cost,
                "supervisor_cost_usd": supervisor_cost,
                "incremental_cost_usd": incremental_cost,
            },
        },
    )
    roi_analysis = _write_json_artifact(
        evidence_root,
        "artifacts/roi-analysis.json",
        {
            "schema_version": "supervisor-roi-analysis/v2",
            "comparison": "B_vs_C",
            "causal_analysis_sha256": causal_evidence["analysis_sha256"],
            "lineage": {
                "business_value_protocol": business_value_protocol,
                "cost_provenance": cost_provenance,
            },
            "measurement": {
                "task_count": 15,
                "baseline_successes": 0,
                "supervisor_successes": 15,
                "baseline_cost_usd": baseline_cost,
                "supervisor_cost_usd": supervisor_cost,
                "value_per_success_usd": 2.0,
            },
            "result": {
                "incremental_successes": 15,
                "incremental_cost_usd": incremental_cost,
                "cost_per_incremental_success_usd": (
                    cost_per_incremental_success
                ),
                "break_even_value_per_success_usd": (
                    cost_per_incremental_success
                ),
                "incremental_value_usd": incremental_value,
                "net_value_usd": net_value,
                "positive_roi": True,
            },
        },
    )
    roi_authority = _externally_attested_descriptor(
        roi_analysis,
        evidence_kind="roi_analysis",
    )
    bundle["operating_cost"] = {
        "measured": True,
        "cost_usd": supervisor_cost,
        "supports_positive_roi": True,
        "analysis_ref": roi_authority["ref"],
        "analysis_sha256": roi_authority["sha256"],
        "attestation": roi_authority["attestation"],
    }
    return bundle, ledger_resolver


def _auto_improvement_authoritative_bundle(
    evidence_root: Path,
) -> tuple[
    dict[str, object],
    _FixtureAuthority,
]:
    bundle, ledger_resolver = _roi_authoritative_bundle(evidence_root)
    change_id = "policy-change-fixture-v1"
    control_policy_sha256 = _sha256_text("control-policy-v1")
    candidate_policy_sha256 = _sha256_text("candidate-policy-v1")
    holdout_dataset_sha256 = _sha256_text("sealed-holdout-v1")
    frozen_control = _write_json_artifact(
        evidence_root,
        "artifacts/frozen-control.json",
        {
            "schema_version": "supervisor-frozen-control-receipt/v1",
            "change_id": change_id,
            "control_policy_sha256": control_policy_sha256,
            "candidate_policy_sha256": candidate_policy_sha256,
            "frozen_at_ms": 3_000,
            "frozen": True,
        },
    )
    sealed_holdout = _write_json_artifact(
        evidence_root,
        "artifacts/sealed-holdout.json",
        {
            "schema_version": "supervisor-sealed-holdout-receipt/v1",
            "change_id": change_id,
            "dataset_sha256": holdout_dataset_sha256,
            "access_log_sha256": _sha256_text("holdout-access-log-v1"),
            "sealed_at_ms": 3_100,
            "opened_at_ms": 4_000,
            "evaluation_completed_at_ms": 4_500,
            "sealed": True,
        },
    )
    shadow_result = _write_json_artifact(
        evidence_root,
        "artifacts/shadow-result.json",
        {
            "schema_version": "supervisor-shadow-result/v1",
            "change_id": change_id,
            "control_policy_sha256": control_policy_sha256,
            "candidate_policy_sha256": candidate_policy_sha256,
            "holdout_dataset_sha256": holdout_dataset_sha256,
            "frozen_control_receipt_sha256": frozen_control["sha256"],
            "sealed_holdout_receipt_sha256": sealed_holdout["sha256"],
            "started_at_ms": 3_900,
            "completed_at_ms": 4_500,
            "task_count": 30,
            "control_successes": 20,
            "candidate_successes": 22,
            "guardrail_regressions": 0,
            "passed": True,
        },
    )
    rollback_receipt = _write_json_artifact(
        evidence_root,
        "artifacts/rollback-receipt.json",
        {
            "schema_version": "supervisor-rollback-receipt/v1",
            "change_id": change_id,
            "candidate_policy_sha256": candidate_policy_sha256,
            "restored_control_policy_sha256": control_policy_sha256,
            "frozen_control_receipt_sha256": frozen_control["sha256"],
            "rollback_plan_sha256": _sha256_text("rollback-plan-v1"),
            "exercise": "restore_frozen_control",
            "tested_at_ms": 4_600,
            "restore_seconds": 30.0,
            "passed": True,
        },
    )
    human_approval = _write_json_artifact(
        evidence_root,
        "artifacts/human-approval.json",
        {
            "schema_version": "supervisor-human-approval-receipt/v1",
            "change_id": change_id,
            "approver_type": "human",
            "approver": {
                "name": "Ada Lovelace",
                "identity": "ada.lovelace@example.com",
                "role": "Release Owner",
            },
            "decision": "approved",
            "approved_at_ms": 4_700,
            "shadow_result_sha256": shadow_result["sha256"],
            "sealed_holdout_receipt_sha256": sealed_holdout["sha256"],
            "rollback_receipt_sha256": rollback_receipt["sha256"],
            "approved": True,
        },
    )
    canary = _write_json_artifact(
        evidence_root,
        "artifacts/canary-result.json",
        {
            "schema_version": "supervisor-canary-result/v1",
            "change_id": change_id,
            "candidate_policy_sha256": candidate_policy_sha256,
            "deployed_at_ms": 4_800,
            "completed_at_ms": 5_000,
            "traffic_fraction": 0.10,
            "sample_size": 50,
            "control_successes": 40,
            "candidate_successes": 42,
            "guardrail_regressions": 0,
            "shadow_result_sha256": shadow_result["sha256"],
            "human_approval_receipt_sha256": human_approval["sha256"],
            "rollback_receipt_sha256": rollback_receipt["sha256"],
            "passed": True,
        },
    )
    bundle.update(
        {
            "frozen_control": {"frozen": True, **frozen_control},
            "sealed_holdout": {"sealed": True, **sealed_holdout},
            "shadow_result": {"passed": True, **shadow_result},
            "human_approval": {"approved": True, **human_approval},
            "canary": {"passed": True, **canary},
            "rollback_receipt": {
                "passed": True,
                **rollback_receipt,
            },
        }
    )
    authority_manifest = _write_json_artifact(
        evidence_root,
        "artifacts/auto-improvement-authority-manifest.json",
        {
            "schema_version": (
                "supervisor-auto-improvement-authority-manifest/v1"
            ),
            "change_id": change_id,
            "receipt_hashes": {
                name: descriptor["sha256"]
                for name, descriptor in (
                    ("frozen_control", frozen_control),
                    ("sealed_holdout", sealed_holdout),
                    ("shadow_result", shadow_result),
                    ("human_approval", human_approval),
                    ("canary", canary),
                    ("rollback_receipt", rollback_receipt),
                )
            },
        },
    )
    bundle["auto_improvement_authority_manifest"] = (
        _externally_attested_descriptor(
            authority_manifest,
            evidence_kind="auto_improvement_authority_manifest",
        )
    )
    return bundle, ledger_resolver


def _rewrite_roi_analysis(
    evidence_root: Path,
    bundle: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
) -> None:
    operating_cost = bundle["operating_cost"]
    assert isinstance(operating_cost, dict)
    analysis_ref = operating_cost["analysis_ref"]
    assert isinstance(analysis_ref, str)
    analysis_path = evidence_root / analysis_ref
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    mutate(analysis)
    content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    analysis_path.write_bytes(content)
    operating_cost["analysis_sha256"] = sha256(content).hexdigest()


def _rewrite_roi_lineage_document(
    evidence_root: Path,
    bundle: dict[str, object],
    name: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    operating_cost = bundle["operating_cost"]
    assert isinstance(operating_cost, dict)
    analysis_path = evidence_root / str(operating_cost["analysis_ref"])
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    descriptor = analysis["lineage"][name]
    document_path = evidence_root / descriptor["ref"]
    document = json.loads(document_path.read_text(encoding="utf-8"))
    mutate(document)
    document_content = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    document_path.write_bytes(document_content)
    descriptor["sha256"] = sha256(document_content).hexdigest()
    if name == "business_value_protocol":
        cost_descriptor = analysis["lineage"]["cost_provenance"]
        cost_path = evidence_root / cost_descriptor["ref"]
        cost_document = json.loads(cost_path.read_text(encoding="utf-8"))
        cost_document["business_value_protocol_sha256"] = descriptor[
            "sha256"
        ]
        cost_content = json.dumps(
            cost_document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        cost_path.write_bytes(cost_content)
        cost_descriptor["sha256"] = sha256(cost_content).hexdigest()
    analysis_content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    analysis_path.write_bytes(analysis_content)
    operating_cost["analysis_sha256"] = sha256(
        analysis_content
    ).hexdigest()


def _rewrite_replication_study(
    evidence_root: Path,
    bundle: dict[str, object],
    *,
    study_index: int,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    replication = bundle["strata_replication"]
    assert isinstance(replication, dict)
    replication_path = evidence_root / str(replication["analysis_ref"])
    replication_analysis = json.loads(
        replication_path.read_text(encoding="utf-8")
    )
    study_descriptor = replication_analysis["studies"][study_index]
    study_path = evidence_root / study_descriptor["analysis_ref"]
    study_analysis = json.loads(study_path.read_text(encoding="utf-8"))
    mutate(study_analysis)
    study_content = json.dumps(
        study_analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    study_path.write_bytes(study_content)
    study_descriptor["analysis_sha256"] = sha256(study_content).hexdigest()
    replication_content = json.dumps(
        replication_analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    replication_path.write_bytes(replication_content)
    replication["analysis_sha256"] = sha256(replication_content).hexdigest()


def _rewrite_auto_improvement_receipt(
    evidence_root: Path,
    bundle: dict[str, object],
    name: str,
    mutate: Callable[[dict[str, object]], None],
    *,
    preserve_links: bool = True,
) -> None:
    def rewrite(
        receipt_name: str,
        update: Callable[[dict[str, object]], None],
    ) -> None:
        descriptor = bundle[receipt_name]
        assert isinstance(descriptor, dict)
        path = evidence_root / str(descriptor["ref"])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        update(receipt)
        content = json.dumps(
            receipt,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path.write_bytes(content)
        descriptor["sha256"] = sha256(content).hexdigest()

    rewrite(name, mutate)
    if not preserve_links:
        return

    def descriptor_hash(receipt_name: str) -> str:
        descriptor = bundle[receipt_name]
        assert isinstance(descriptor, dict)
        return str(descriptor["sha256"])

    rewrite(
        "shadow_result",
        lambda receipt: receipt.update(
            {
                "frozen_control_receipt_sha256": descriptor_hash(
                    "frozen_control"
                ),
                "sealed_holdout_receipt_sha256": descriptor_hash(
                    "sealed_holdout"
                ),
            }
        ),
    )
    rewrite(
        "rollback_receipt",
        lambda receipt: receipt.update(
            {
                "frozen_control_receipt_sha256": descriptor_hash(
                    "frozen_control"
                )
            }
        ),
    )
    rewrite(
        "human_approval",
        lambda receipt: receipt.update(
            {
                "shadow_result_sha256": descriptor_hash(
                    "shadow_result"
                ),
                "sealed_holdout_receipt_sha256": descriptor_hash(
                    "sealed_holdout"
                ),
                "rollback_receipt_sha256": descriptor_hash(
                    "rollback_receipt"
                ),
            }
        ),
    )
    rewrite(
        "canary",
        lambda receipt: receipt.update(
            {
                "shadow_result_sha256": descriptor_hash(
                    "shadow_result"
                ),
                "human_approval_receipt_sha256": descriptor_hash(
                    "human_approval"
                ),
                "rollback_receipt_sha256": descriptor_hash(
                    "rollback_receipt"
                ),
            }
        ),
    )


def _rewrite_auto_improvement_authority_manifest(
    evidence_root: Path,
    bundle: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
    *,
    resign: bool,
) -> None:
    descriptor = bundle["auto_improvement_authority_manifest"]
    assert isinstance(descriptor, dict)
    path = evidence_root / str(descriptor["ref"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    content = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(content)
    descriptor["sha256"] = sha256(content).hexdigest()
    if resign:
        _resign_external_descriptor(
            descriptor,
            evidence_kind="auto_improvement_authority_manifest",
        )


def _rewrite_analysis(
    evidence_root: Path,
    bundle: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    ref = result["analysis_ref"]
    assert isinstance(ref, str)
    path = evidence_root / ref
    analysis = json.loads(path.read_text(encoding="utf-8"))
    mutate(analysis)
    content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(content)
    result["analysis_sha256"] = sha256(content).hexdigest()


def _resign_external_descriptor(
    descriptor: dict[str, object],
    *,
    evidence_kind: str,
) -> None:
    signed = _externally_attested_descriptor(
        {
            "ref": str(descriptor["ref"]),
            "sha256": str(descriptor["sha256"]),
        },
        evidence_kind=evidence_kind,
    )
    descriptor["attestation"] = signed["attestation"]


def _rewrite_experiment_manifest(
    evidence_root: Path,
    bundle: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
    *,
    resign: bool,
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    descriptor = result["experiment_manifest"]
    assert isinstance(descriptor, dict)
    path = evidence_root / str(descriptor["ref"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    content = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(content)
    descriptor["sha256"] = sha256(content).hexdigest()
    if resign:
        _resign_external_descriptor(
            descriptor,
            evidence_kind="experiment_evidence_manifest",
        )


def _refresh_experiment_manifest(
    evidence_root: Path,
    bundle: dict[str, object],
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    analysis_descriptor = {
        "ref": str(result["analysis_ref"]),
        "sha256": str(result["analysis_sha256"]),
    }
    analysis_path = evidence_root / analysis_descriptor["ref"]
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    def refresh(manifest: dict[str, object]) -> None:
        lineage = analysis["lineage"]
        assert isinstance(lineage, dict)
        manifest["artifacts"] = {
            "analysis": analysis_descriptor,
            **{
                name: lineage[name]
                for name in (
                    "assignments",
                    "grades",
                    "trace",
                    "ledger",
                    "verifier",
                    "study",
                )
            },
        }
        manifest["analysis_hashes"] = {
            "analysis_sha256": analysis_descriptor["sha256"],
            "design_sha256": _sha256_json(analysis["design"]),
            "task_rows_sha256": _sha256_json(analysis["task_rows"]),
            "result_sha256": _sha256_json(analysis["result"]),
        }

    _rewrite_experiment_manifest(
        evidence_root,
        bundle,
        refresh,
        resign=True,
    )


def _rewrite_pilot_analysis(
    evidence_root: Path,
    bundle: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    analysis_path = evidence_root / str(result["analysis_ref"])
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    study_descriptor = analysis["lineage"]["study"]
    study_path = evidence_root / study_descriptor["ref"]
    study = json.loads(study_path.read_text(encoding="utf-8"))

    pilot_analysis_descriptor = study["pilot"]["analysis"]
    pilot_analysis_path = (
        evidence_root / pilot_analysis_descriptor["ref"]
    )
    pilot_analysis = json.loads(
        pilot_analysis_path.read_text(encoding="utf-8")
    )
    mutate(pilot_analysis)
    pilot_content = json.dumps(
        pilot_analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    pilot_analysis_path.write_bytes(pilot_content)
    pilot_analysis_descriptor["sha256"] = sha256(
        pilot_content
    ).hexdigest()

    confirmation_protocol_descriptor = study["confirmation"]["protocol"]
    confirmation_protocol_path = (
        evidence_root / confirmation_protocol_descriptor["ref"]
    )
    confirmation_protocol = json.loads(
        confirmation_protocol_path.read_text(encoding="utf-8")
    )
    confirmation_protocol["pilot_analysis_sha256"] = (
        pilot_analysis_descriptor["sha256"]
    )
    confirmation_protocol_content = json.dumps(
        confirmation_protocol,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    confirmation_protocol_path.write_bytes(confirmation_protocol_content)
    confirmation_protocol_descriptor["sha256"] = sha256(
        confirmation_protocol_content
    ).hexdigest()

    confirmation_roster_descriptor = study["confirmation"]["roster"]
    confirmation_roster_path = (
        evidence_root / confirmation_roster_descriptor["ref"]
    )
    confirmation_roster = json.loads(
        confirmation_roster_path.read_text(encoding="utf-8")
    )
    confirmation_roster["protocol_sha256"] = (
        confirmation_protocol_descriptor["sha256"]
    )
    confirmation_roster_content = json.dumps(
        confirmation_roster,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    confirmation_roster_path.write_bytes(confirmation_roster_content)
    confirmation_roster_descriptor["sha256"] = sha256(
        confirmation_roster_content
    ).hexdigest()

    study["derivation"]["pilot_analysis_sha256"] = (
        pilot_analysis_descriptor["sha256"]
    )
    study["derivation"]["confirmation_protocol_sha256"] = (
        confirmation_protocol_descriptor["sha256"]
    )
    study_content = json.dumps(
        study,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    study_path.write_bytes(study_content)
    study_descriptor["sha256"] = sha256(study_content).hexdigest()

    analysis_content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    analysis_path.write_bytes(analysis_content)
    result["analysis_sha256"] = sha256(analysis_content).hexdigest()
    _refresh_experiment_manifest(evidence_root, bundle)


def _rewrite_lineage_document(
    evidence_root: Path,
    bundle: dict[str, object],
    name: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    analysis_ref = result["analysis_ref"]
    assert isinstance(analysis_ref, str)
    analysis_path = evidence_root / analysis_ref
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    descriptor = analysis["lineage"][name]
    document_path = evidence_root / descriptor["ref"]
    document = json.loads(document_path.read_text(encoding="utf-8"))
    mutate(document)
    document_content = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    document_path.write_bytes(document_content)
    descriptor["sha256"] = sha256(document_content).hexdigest()

    if name in {"assignments", "grades"}:
        ledger_descriptor = analysis["lineage"]["ledger"]
        ledger_path = evidence_root / ledger_descriptor["ref"]
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger[f"{name[:-1]}_artifact_sha256"] = descriptor["sha256"]
        ledger_content = json.dumps(
            ledger,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ledger_path.write_bytes(ledger_content)
        ledger_descriptor["sha256"] = sha256(ledger_content).hexdigest()

    analysis_content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    analysis_path.write_bytes(analysis_content)
    result["analysis_sha256"] = sha256(analysis_content).hexdigest()


def _rewrite_study_artifact(
    evidence_root: Path,
    bundle: dict[str, object],
    *,
    phase: str,
    artifact_name: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(result, dict)
    analysis_path = evidence_root / str(result["analysis_ref"])
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    study_descriptor = analysis["lineage"]["study"]
    study_path = evidence_root / study_descriptor["ref"]
    study = json.loads(study_path.read_text(encoding="utf-8"))
    artifact_descriptor = study[phase][artifact_name]
    artifact_path = evidence_root / artifact_descriptor["ref"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    mutate(artifact)
    artifact_content = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifact_path.write_bytes(artifact_content)
    artifact_descriptor["sha256"] = sha256(artifact_content).hexdigest()

    study_content = json.dumps(
        study,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    study_path.write_bytes(study_content)
    study_descriptor["sha256"] = sha256(study_content).hexdigest()
    analysis_content = json.dumps(
        analysis,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    analysis_path.write_bytes(analysis_content)
    result["analysis_sha256"] = sha256(analysis_content).hexdigest()


def test_fixture_replay_bundle_resolves_to_l1(tmp_path: Path) -> None:
    assert (
        ClaimGate.max_claim_level(
            _fixture_replay_bundle(tmp_path),
            evidence_root=tmp_path,
        )
        == ClaimLevel.L1
    )


def test_valid_looking_evidence_without_a_resolver_fails_closed(
    tmp_path: Path,
) -> None:
    assert ClaimGate.max_claim_level(_fixture_replay_bundle(tmp_path)) is None


def test_missing_or_hash_mismatched_trace_cannot_raise_l1(tmp_path: Path) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    trace_path = tmp_path / "artifacts/fixture-replay-trace.jsonl"

    trace_path.unlink()
    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L0
    )

    trace_path.write_bytes(b'{"event":"tampered"}\n')
    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L0
    )


def test_unresolved_declared_hash_cannot_raise_l0(tmp_path: Path) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    hashes = bundle["hashes"]
    assert isinstance(hashes, dict)
    hashes["unresolved_sha256"] = "f" * 64

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) is None


def test_repeated_declared_digest_can_share_one_verified_artifact(
    tmp_path: Path,
) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    hashes = bundle["hashes"]
    assert isinstance(hashes, dict)
    hashes["run_manifest_copy_sha256"] = hashes["run_manifest_sha256"]

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L1
    )


def test_l2_requires_a_trusted_independent_verifier_attestation(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    verifier = bundle["independent_hidden_verifier"]
    assert isinstance(verifier, dict)

    assert (
        _ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L1
    )
    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(),
        )
        == ClaimLevel.L2
    )

    verifier["independent"] = False
    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(),
        )
        == ClaimLevel.L1
    )


def test_self_attested_hidden_verifier_cannot_raise_l2(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    verifier = bundle["independent_hidden_verifier"]
    assert isinstance(verifier, dict)
    result = {
        "ref": str(verifier["result_ref"]),
        "sha256": str(verifier["result_sha256"]),
    }
    bundle["independent_hidden_verifier"] = _attested_hidden_verifier(
        result,
        producer_principal_id=_VERIFIER_PRINCIPAL_ID,
        verifier_principal_id=_VERIFIER_PRINCIPAL_ID,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(),
        )
        == ClaimLevel.L1
    )


def test_forged_independent_verifier_signature_cannot_raise_l2(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    verifier = bundle["independent_hidden_verifier"]
    assert isinstance(verifier, dict)
    attestation = verifier["attestation"]
    assert isinstance(attestation, dict)
    signature = attestation["signature"]
    assert isinstance(signature, dict)
    signature["hmac_sha256"] = "0" * 64

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(),
        )
        == ClaimLevel.L1
    )


def test_higher_levels_fail_closed_when_verifier_is_self_authored(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    verifier = bundle["independent_hidden_verifier"]
    assert isinstance(verifier, dict)
    result = {
        "ref": str(verifier["result_ref"]),
        "sha256": str(verifier["result_sha256"]),
    }
    bundle["independent_hidden_verifier"] = _attested_hidden_verifier(
        result,
        producer_principal_id=_VERIFIER_PRINCIPAL_ID,
        verifier_principal_id=_VERIFIER_PRINCIPAL_ID,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(ledger_resolver),
        )
        == ClaimLevel.L1
    )


def test_opaque_pinned_bytes_and_self_declared_booleans_cannot_raise_l3(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    analysis = _write_artifact(
        tmp_path,
        "artifacts/adversarial-b-vs-c-analysis.json",
        b'{"attacker_controlled":true}\n',
    )
    bundle["randomized_powered_b_vs_c"] = {
        "comparison": "B_vs_C",
        "randomized": True,
        "powered": True,
        "supports_improvement": True,
        "analysis_ref": analysis["ref"],
        "analysis_sha256": analysis["sha256"],
    }

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )


def test_edge_free_80_percent_self_attested_evidence_cannot_raise_l3(
    tmp_path: Path,
) -> None:
    bundle = _causal_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )


def test_edge_free_trace_cannot_raise_l3_even_with_90_percent_power(
    tmp_path: Path,
) -> None:
    bundle = _causal_bundle(
        tmp_path,
        task_count=15,
        target_power=0.90,
        required_discordant_pairs=15,
    )

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )


def test_closed_trace_and_90_percent_design_can_reach_l3(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L3
    )


def test_self_attested_ledger_records_cannot_raise_l3(
    tmp_path: Path,
) -> None:
    bundle = _causal_bundle(
        tmp_path,
        task_count=15,
        target_power=0.90,
        required_discordant_pairs=15,
        closed_trace=True,
    )

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )


def test_stored_ledger_record_must_match_the_authoritative_result(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_lineage_document(
        tmp_path,
        bundle,
        "ledger",
        lambda document: document["runs"][0]["verification"].update(
            {"event_count": 999}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_requires_explicit_pilot_to_confirmation_lineage(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_analysis(
        tmp_path,
        bundle,
        lambda analysis: analysis["lineage"].pop("study"),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_pilot_and_confirmation_rosters_must_be_disjoint_and_bound(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)

    def alias_pilot_task(roster: dict[str, object]) -> None:
        tasks = roster["tasks"]
        assert isinstance(tasks, list)
        first_task = tasks[0]
        assert isinstance(first_task, dict)
        pilot_identity = _task_identity("pilot-task-00")
        first_task["task_identity"] = pilot_identity
        first_task["canonical_task_id"] = canonical_task_identity(
            pilot_identity
        )

    _rewrite_study_artifact(
        tmp_path,
        bundle,
        phase="confirmation",
        artifact_name="roster",
        mutate=alias_pilot_task,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_confirmation_protocol_must_pin_the_powered_design(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_study_artifact(
        tmp_path,
        bundle,
        phase="confirmation",
        artifact_name="protocol",
        mutate=lambda protocol: protocol.update(
            {"powered_design_sha256": "0" * 64}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_requires_every_grade_on_the_authorizing_analysis_path(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)

    def disconnect_one_grade(document: dict[str, object]) -> None:
        edges = document["edges"]
        assert isinstance(edges, list)
        for index, edge in enumerate(edges):
            assert isinstance(edge, dict)
            if (
                edge.get("relation") == "derived_from"
                and ":ANL:" in str(edge.get("source"))
                and ":GRADE:" in str(edge.get("target"))
            ):
                edges.pop(index)
                return
        raise AssertionError("fixture has no analysis-to-grade edge")

    _rewrite_lineage_document(
        tmp_path,
        bundle,
        "trace",
        disconnect_one_grade,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_recomputes_task_rows_from_pinned_grade_lineage(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_analysis(
        tmp_path,
        bundle,
        lambda analysis: analysis["task_rows"][0].update(
            {"b_pass": False}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_recomputes_grade_revision_hashes(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_lineage_document(
        tmp_path,
        bundle,
        "grades",
        lambda document: document["grades"][0]["grade"].update(
            {"passed": False}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_requires_assignment_persistence_before_execution(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_lineage_document(
        tmp_path,
        bundle,
        "assignments",
        lambda document: document["assignments"][0].update(
            {
                "persisted_at_ms": document["assignments"][0][
                    "first_execution_started_at_ms"
                ]
            }
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


def test_l3_recomputes_randomized_assignment_hmac(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_lineage_document(
        tmp_path,
        bundle,
        "assignments",
        lambda document: document["randomization"].update(
            {"hmac_key_hex": "00" * 32}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda analysis: analysis.update(
            {"schema_version": "supervisor-b-vs-c-analysis/v999"}
        ),
        lambda analysis: analysis["lineage"].pop("trace"),
        lambda analysis: analysis["design"]["power"].update(
            {"required_task_count": 1}
        ),
        lambda analysis: analysis["result"]["effect"].update(
            {"estimate": 0.5}
        ),
        lambda analysis: analysis["result"]["test"].update(
            {"p_value": 0.04}
        ),
    ],
)
def test_l3_requires_expected_schema_power_positive_test_and_lineage(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    _rewrite_analysis(tmp_path, bundle, mutate)

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )


@pytest.mark.parametrize(
    "optimizer_unseen_family",
    [
        {"family": "", "pinned": True, "seen_by_optimizer": False},
        {"family": "anthropic", "pinned": True, "seen_by_optimizer": False},
        {"family": "optimizer-holdout", "pinned": False, "seen_by_optimizer": False},
    ],
)
def test_l4_optimizer_unseen_family_is_named_distinct_pinned_and_counted(
    tmp_path: Path,
    optimizer_unseen_family: dict[str, object],
) -> None:
    bundle, ledger_resolver = _portable_authoritative_bundle(tmp_path)
    replication = bundle["strata_replication"]
    assert isinstance(replication, dict)
    model_families = replication["model_families"]
    assert isinstance(model_families, list)
    replication["model_families"] = [
        *model_families[:2],
        optimizer_unseen_family,
    ]

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L3
    )


def test_opaque_replication_bytes_and_self_declared_metadata_cannot_raise_l4(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    replication = _write_json_artifact(
        tmp_path,
        "artifacts/opaque-strata-replication.json",
        {"replicated": True},
    )
    bundle["strata_replication"] = {
        "replicated": True,
        "strata": ["python", "typescript", "unity"],
        "model_families": [
            {
                "family": "anthropic",
                "pinned": True,
                "seen_by_optimizer": True,
            },
            {
                "family": "openai",
                "pinned": True,
                "seen_by_optimizer": True,
            },
            {
                "family": "google",
                "pinned": True,
                "seen_by_optimizer": False,
            },
        ],
        "analysis_ref": replication["ref"],
        "analysis_sha256": replication["sha256"],
    }

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L3
    )


def test_versioned_semantic_replications_can_reach_l4(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _portable_authoritative_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L4
    )


def test_l4_revalidates_each_replication_study(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _portable_authoritative_bundle(tmp_path)
    _rewrite_replication_study(
        tmp_path,
        bundle,
        study_index=0,
        mutate=lambda analysis: analysis.update(
            {"schema_version": "supervisor-b-vs-c-analysis/v999"}
        ),
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L3
    )


def test_opaque_roi_bytes_and_support_boolean_cannot_raise_l5(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _portable_authoritative_bundle(tmp_path)
    opaque_analysis = _write_artifact(
        tmp_path,
        "artifacts/opaque-roi-analysis.json",
        b"attacker-controlled ROI bytes",
    )
    bundle["operating_cost"] = {
        "measured": True,
        "cost_usd": 12.5,
        "supports_positive_roi": True,
        "analysis_ref": opaque_analysis["ref"],
        "analysis_sha256": opaque_analysis["sha256"],
    }

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L4
    )


def test_versioned_recomputed_positive_roi_can_reach_l5(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _roi_authoritative_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L5
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda analysis: analysis.update(
            {"schema_version": "supervisor-roi-analysis/v999"}
        ),
        lambda analysis: analysis["measurement"].update(
            {"baseline_cost_usd": -1.0}
        ),
        lambda analysis: analysis["measurement"].update(
            {"supervisor_successes": 16}
        ),
        lambda analysis: analysis["result"].update(
            {"incremental_successes": 14}
        ),
        lambda analysis: analysis["result"].update(
            {"cost_per_incremental_success_usd": 0.5}
        ),
        lambda analysis: analysis["measurement"].update(
            {"value_per_success_usd": 1.0}
        ),
    ],
)
def test_l5_rejects_unversioned_impossible_or_miscalculated_roi(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    bundle, ledger_resolver = _roi_authoritative_bundle(tmp_path)
    _rewrite_roi_analysis(tmp_path, bundle, mutate)

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L4
    )


@pytest.mark.parametrize(
    ("lineage_name", "mutate"),
    [
        (
            "business_value_protocol",
            lambda document: document.update(
                {"registered_at_ms": 2_000}
            ),
        ),
        (
            "cost_provenance",
            lambda document: document["components"].pop("risk"),
        ),
        (
            "cost_provenance",
            lambda document: document["components"]["compute"][
                "supervisor"
            ].update({"reported_cost_usd": 1.0}),
        ),
        (
            "cost_provenance",
            lambda document: document.update(
                {"measurement_started_at_ms": 500}
            ),
        ),
    ],
)
def test_l5_requires_preregistered_value_and_complete_cost_provenance(
    tmp_path: Path,
    lineage_name: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    bundle, ledger_resolver = _roi_authoritative_bundle(tmp_path)
    _rewrite_roi_lineage_document(
        tmp_path,
        bundle,
        lineage_name,
        mutate,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L4
    )


def test_opaque_self_declared_control_receipts_cannot_raise_l6(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _roi_authoritative_bundle(tmp_path)
    for name, state_key in (
        ("frozen_control", "frozen"),
        ("sealed_holdout", "sealed"),
        ("shadow_result", "passed"),
        ("human_approval", "approved"),
        ("canary", "passed"),
        ("rollback_receipt", "passed"),
    ):
        descriptor = _write_json_artifact(
            tmp_path,
            f"artifacts/opaque-{name}.json",
            {state_key: True},
        )
        bundle[name] = {state_key: True, **descriptor}

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L5
    )


def test_versioned_linked_control_receipts_can_reach_l6(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _auto_improvement_authoritative_bundle(
        tmp_path
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L6
    )


@pytest.mark.parametrize(
    ("receipt_name", "mutate"),
    [
        (
            "shadow_result",
            lambda receipt: receipt.update(
                {"guardrail_regressions": 1}
            ),
        ),
        (
            "human_approval",
            lambda receipt: receipt["approver"].update({"name": ""}),
        ),
        (
            "canary",
            lambda receipt: receipt.update({"traffic_fraction": 1.0}),
        ),
        (
            "rollback_receipt",
            lambda receipt: receipt.update(
                {"restored_control_policy_sha256": "0" * 64}
            ),
        ),
    ],
)
def test_l6_revalidates_shadow_human_canary_and_rollback_receipts(
    tmp_path: Path,
    receipt_name: str,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    bundle, ledger_resolver = _auto_improvement_authoritative_bundle(
        tmp_path
    )
    _rewrite_auto_improvement_receipt(
        tmp_path,
        bundle,
        receipt_name,
        mutate,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L5
    )


def test_l6_rejects_a_valid_receipt_linked_to_the_wrong_approval(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _auto_improvement_authoritative_bundle(
        tmp_path
    )
    _rewrite_auto_improvement_receipt(
        tmp_path,
        bundle,
        "canary",
        lambda receipt: receipt.update(
            {"human_approval_receipt_sha256": "0" * 64}
        ),
        preserve_links=False,
    )

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L5
    )


@pytest.mark.parametrize(
    "missing_receipt",
    [
        "frozen_control",
        "sealed_holdout",
        "shadow_result",
        "human_approval",
        "canary",
        "rollback_receipt",
    ],
)
def test_l6_requires_every_control_receipt(
    tmp_path: Path,
    missing_receipt: str,
) -> None:
    bundle, ledger_resolver = _auto_improvement_authoritative_bundle(
        tmp_path
    )
    del bundle[missing_receipt]

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L5
    )


def test_low_level_claim_id_cannot_override_improvement_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        UnsupportedClaimError,
        match="Supervisor improves outcomes requires L3; evidence supports L2",
    ):
        ClaimGate.validate_report(
            {
                "claims": [
                    {
                        "claim_id": "CLAIM-HARNESS-L0-INTEGRITY",
                        "text": "Supervisor improves outcomes",
                    }
                ]
            },
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )


def test_current_tracer_evidence_remains_capped_at_l2(tmp_path: Path) -> None:
    bundle = _outcome_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )
    assert ClaimGate.derived_claim_flags(
        bundle,
        evidence_root=tmp_path,
    ) == {
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
    }


def test_custom_evidence_resolver_can_supply_verified_bytes(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    resolved = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_resolver=resolved.get,
        )
        == ClaimLevel.L2
    )


def test_mismatched_causal_analysis_hash_stops_at_l2(tmp_path: Path) -> None:
    bundle, ledger_resolver = _authoritative_causal_bundle(tmp_path)
    analysis = bundle["randomized_powered_b_vs_c"]
    assert isinstance(analysis, dict)
    analysis["analysis_sha256"] = "0" * 64

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )
        == ClaimLevel.L2
    )
