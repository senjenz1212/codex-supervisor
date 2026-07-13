from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import RFC_4122, UUID, uuid4

import pytest

from supervisor.dual_agent_lead import PlanningArtifact
from supervisor.grade_revisions import GradeBook, GradeRevision, RunEnvelopeRef
from supervisor.planning_validator import (
    build_trace_closure_binding,
    validate_planning_artifacts,
)
from supervisor.task_environment import Grade
from supervisor.trace_graph import (
    ClosureRule,
    EdgeType,
    InvalidTraceIdentity,
    NodeType,
    ProvKind,
    TRACE_CLOSURE_BINDING_ATTRIBUTE,
    TraceClosureBinding,
    TraceDecisionGradeValidation,
    TraceEdge,
    TraceGraph,
    TraceGraphError,
    TraceGraphStore,
    TraceIdentity,
    TraceNode,
    TracePlanningArtifactRef,
    TraceWaiver,
    canonical_revision_hash,
    new_trace_instance_id,
    trace_instance_id_from_hash,
)


def test_trace_identity_is_namespaced_versioned_and_rejects_bare_legacy_ids():
    revision_a = canonical_revision_hash({"body": "same", "order": 1})
    revision_b = canonical_revision_hash({"order": 1, "body": "same"})
    identity = TraceIdentity(
        namespace="harness-v1",
        node_type=NodeType.REQ,
        logical_id="REQ-HARNESS-001",
        revision_hash=revision_a,
        instance_id="018f8f86-2f20-7b9d-8000-000000000001",
    )

    assert revision_a == revision_b
    assert len(identity.revision_hash) == 64
    assert str(UUID(identity.instance_id)) == identity.instance_id
    assert identity.canonical_key.startswith(
        "harness-v1:REQ:REQ-HARNESS-001@"
    )

    with pytest.raises(InvalidTraceIdentity, match="RFC UUIDv7"):
        TraceIdentity(
            namespace="harness-v1",
            node_type=NodeType.REQ,
            logical_id="REQ-HARNESS-UUID4",
            revision_hash=revision_a,
            instance_id=str(uuid4()),
        )

    with pytest.raises(InvalidTraceIdentity, match="compatibility-only"):
        TraceIdentity(
            namespace="agent-supervisor",
            node_type=NodeType.REQ,
            logical_id="P1",
            revision_hash=revision_a,
            instance_id="018f8f86-2f20-7b9d-8000-000000000002",
        )

    agent_p1 = TraceIdentity.from_legacy(
        namespace="agent-supervisor",
        node_type=NodeType.REQ,
        local_id="P1",
        revision={"promise": "first"},
        instance_id="018f8f86-2f20-7b9d-8000-000000000003",
    )
    benchmark_p1 = TraceIdentity.from_legacy(
        namespace="powered-benchmark",
        node_type=NodeType.REQ,
        local_id="P1",
        revision={"promise": "second"},
        instance_id="018f8f86-2f20-7b9d-8000-000000000004",
    )

    assert agent_p1.logical_id == "REQ-agent-supervisor:P1"
    assert benchmark_p1.logical_id == "REQ-powered-benchmark:P1"
    assert agent_p1.canonical_key != benchmark_p1.canonical_key


def test_trace_instance_generator_emits_rfc_uuidv7_values():
    before_ms = int(time.time_ns() // 1_000_000)
    generated = [UUID(new_trace_instance_id()) for _ in range(20)]
    after_ms = int(time.time_ns() // 1_000_000)

    assert len(set(generated)) == len(generated)
    assert all(value.version == 7 for value in generated)
    assert all(value.variant == RFC_4122 for value in generated)
    assert all(
        before_ms <= value.int >> 80 <= after_ms
        for value in generated
    )


def _node(
    node_type: NodeType,
    logical_id: str,
    instance_number: int,
    *,
    revision_hash: str | None = None,
    **kwargs,
) -> TraceNode:
    exact_revision_hash = revision_hash or canonical_revision_hash(
        {"node_type": node_type.value, "logical_id": logical_id}
    )
    return TraceNode(
        identity=TraceIdentity(
            namespace="harness-v1",
            node_type=node_type,
            logical_id=logical_id,
            revision_hash=exact_revision_hash,
            instance_id=trace_instance_id_from_hash(
                timestamp_ms=1_720_000_000_000 + instance_number,
                content_hash=canonical_revision_hash(
                    {
                        "node_type": node_type.value,
                        "logical_id": logical_id,
                        "instance_number": instance_number,
                    }
                ),
                domain="test-trace-node",
            ),
        ),
        **kwargs,
    )


def _gradebook_revision(
    *,
    gradebook: GradeBook | None = None,
    supersedes_grade_id: str | None = None,
    passed: bool = True,
    version: str = "1.0",
) -> tuple[GradeBook, GradeRevision]:
    authority = gradebook or GradeBook(":memory:")
    frozen_result_hash = canonical_revision_hash("trace-frozen-result")
    run = RunEnvelopeRef(
        run_id="trace-run",
        run_envelope_hash=canonical_revision_hash("trace-run-envelope"),
        frozen_result_hash=frozen_result_hash,
    )
    revision = authority.append_grade(
        run=run,
        grade=Grade(
            verifier_id="VERIFIER-HARNESS-001",
            verifier_version=version,
            verifier_hash=canonical_revision_hash(
                {"verifier": "pytest", "version": version}
            ),
            frozen_result_hash=frozen_result_hash,
            passed=passed,
            score=1.0 if passed else 0.0,
            evidence={"tests": "passed" if passed else "failed"},
        ),
        verifier_config_hash=canonical_revision_hash(
            {"config": version}
        ),
        supersedes_grade_id=supersedes_grade_id,
    )
    return authority, revision


def _closed_graph(
    *,
    gradebook: GradeBook | None = None,
    grade_revision: GradeRevision | None = None,
) -> tuple[TraceGraph, dict[NodeType, TraceNode]]:
    if grade_revision is None:
        gradebook, grade_revision = _gradebook_revision(
            gradebook=gradebook
        )
    elif gradebook is None:
        raise AssertionError("grade_revision requires its authoritative GradeBook")
    nodes = {
        NodeType.OBJ: _node(NodeType.OBJ, "OBJ-HARNESS-001", 1),
        NodeType.REQ: _node(NodeType.REQ, "REQ-HARNESS-001", 2),
        NodeType.TEST: _node(NodeType.TEST, "TEST-HARNESS-001", 3),
        NodeType.ASN: _node(NodeType.ASN, "ASN-HARNESS-001", 4),
        NodeType.RUN: _node(
            NodeType.RUN,
            "RUN-HARNESS-001",
            5,
            pinned=True,
        ),
        NodeType.ART: _node(
            NodeType.ART,
            "ART-HARNESS-001",
            6,
            runtime_evidence=True,
        ),
        NodeType.GRADE: _node(
            NodeType.GRADE,
            "GRADE-HARNESS-001",
            7,
            verifier_id=grade_revision.verifier_id,
            verifier_revision_hash=(
                grade_revision.verifier_implementation_hash
            ),
            attributes={
                "grade_id": grade_revision.grade_id,
                "grade_revision_hash": grade_revision.revision_hash,
            },
            revision_hash=grade_revision.revision_hash,
        ),
        NodeType.ANL: _node(NodeType.ANL, "ANL-HARNESS-001", 8),
        NodeType.DEC: _node(
            NodeType.DEC,
            "DEC-HARNESS-001",
            9,
            attributes={
                "grade_citations": [
                    {
                        "grade_id": grade_revision.grade_id,
                        "revision_hash": grade_revision.revision_hash,
                        "acknowledged_invalidation_hashes": [],
                    }
                ]
            },
        ),
        NodeType.PROMOTION: _node(
            NodeType.PROMOTION,
            "PROMOTION-HARNESS-001",
            10,
        ),
    }
    edges = (
        TraceEdge(nodes[NodeType.REQ].identity, EdgeType.IMPLEMENTS, nodes[NodeType.OBJ].identity),
        TraceEdge(nodes[NodeType.TEST].identity, EdgeType.TESTS, nodes[NodeType.REQ].identity),
        TraceEdge(nodes[NodeType.ASN].identity, EdgeType.SUPPORTS, nodes[NodeType.TEST].identity),
        TraceEdge(nodes[NodeType.RUN].identity, EdgeType.ASSIGNED_BY, nodes[NodeType.ASN].identity),
        TraceEdge(nodes[NodeType.ART].identity, EdgeType.DERIVED_FROM, nodes[NodeType.RUN].identity),
        TraceEdge(nodes[NodeType.GRADE].identity, EdgeType.EVALUATES, nodes[NodeType.ART].identity),
        TraceEdge(nodes[NodeType.ANL].identity, EdgeType.DERIVED_FROM, nodes[NodeType.GRADE].identity),
        TraceEdge(nodes[NodeType.DEC].identity, EdgeType.DERIVED_FROM, nodes[NodeType.ANL].identity),
        TraceEdge(nodes[NodeType.PROMOTION].identity, EdgeType.PROMOTES, nodes[NodeType.DEC].identity),
    )
    return (
        TraceGraph(
            nodes=nodes.values(),
            edges=edges,
            decision_grade_validator=gradebook,
        ),
        nodes,
    )


def _binding(
    *,
    task_id: str = "trace-001-graph-20260711",
    run_id: str = "trace-001-run",
    gate: str = "execution",
    planning_artifacts: tuple[TracePlanningArtifactRef, ...] = (),
) -> TraceClosureBinding:
    return TraceClosureBinding(
        task_id=task_id,
        run_id=run_id,
        gate=gate,
        planning_artifacts=planning_artifacts,
    )


def _bind_decision(
    graph: TraceGraph,
    nodes: dict[NodeType, TraceNode],
    binding: TraceClosureBinding,
    *,
    grade_citations: list[dict] | None = None,
) -> TraceGraph:
    decision = nodes[NodeType.DEC]
    attributes = decision.to_dict()["attributes"]
    attributes[TRACE_CLOSURE_BINDING_ATTRIBUTE] = binding.to_dict()
    if grade_citations is not None:
        attributes["grade_citations"] = grade_citations
    bound_decision = replace(decision, attributes=attributes)
    return TraceGraph(
        nodes=(
            bound_decision
            if node.identity == decision.identity
            else node
            for node in graph.nodes
        ),
        edges=graph.edges,
        waivers=graph.waivers,
        decision_grade_validator=graph.decision_grade_validator,
    )


def test_closed_graph_returns_objective_to_promotion_trace_and_passes_closure():
    graph, nodes = _closed_graph()

    result = graph.validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    trace = graph.promotion_trace(nodes[NodeType.PROMOTION].identity)

    assert result.ok, result.to_dict()
    assert [node.identity.node_type for node in trace] == [
        NodeType.OBJ,
        NodeType.REQ,
        NodeType.TEST,
        NodeType.ASN,
        NodeType.RUN,
        NodeType.ART,
        NodeType.GRADE,
        NodeType.ANL,
        NodeType.DEC,
        NodeType.PROMOTION,
    ]
    assert nodes[NodeType.OBJ].prov_kind is ProvKind.ENTITY
    assert nodes[NodeType.RUN].prov_kind is ProvKind.ACTIVITY
    assert nodes[NodeType.ASN].prov_kind is ProvKind.AGENT


def test_closure_rejects_closed_graph_bound_to_an_unrelated_workflow():
    graph, nodes = _closed_graph()
    artifact_ref = TracePlanningArtifactRef(
        kind="implementation_plan",
        path="/repo/docs/implementation-plan.md",
        sha256=canonical_revision_hash("expected implementation plan"),
    )
    graph_binding = _binding(planning_artifacts=(artifact_ref,))
    graph = _bind_decision(graph, nodes, graph_binding)
    expected_bindings = (
        replace(graph_binding, task_id="other-task"),
        replace(graph_binding, run_id="other-run"),
        replace(graph_binding, gate="outcome_review"),
        replace(
            graph_binding,
            planning_artifacts=(
                replace(
                    artifact_ref,
                    sha256=canonical_revision_hash(
                        "different implementation plan"
                    ),
                ),
            ),
        ),
    )

    for expected_binding in expected_bindings:
        result = graph.validate_closure(
            now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            expected_binding=expected_binding,
        )

        assert not result.ok
        assert any(
            finding.rule is ClosureRule.DECISION_CONTEXT_MATCHES
            and finding.node == nodes[NodeType.DEC].identity
            for finding in result.findings
        )


def test_closure_uses_injected_validator_to_reject_superseded_grade_citation():
    gradebook, old_grade = _gradebook_revision(passed=False, version="1.0")
    _gradebook_revision(
        gradebook=gradebook,
        supersedes_grade_id=old_grade.grade_id,
        passed=True,
        version="2.0",
    )
    graph, nodes = _closed_graph(
        gradebook=gradebook,
        grade_revision=old_grade,
    )
    binding = _binding()
    graph = _bind_decision(
        graph,
        nodes,
        binding,
        grade_citations=[
            {
                "grade_id": old_grade.grade_id,
                "revision_hash": old_grade.revision_hash,
                "acknowledged_invalidation_hashes": [],
            }
        ],
    )

    result = graph.validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        expected_binding=binding,
        decision_grade_validator=gradebook,
    )

    assert not result.ok
    assert any(
        finding.rule is ClosureRule.DECISION_GRADE_CITATIONS_CURRENT
        and "stale_grade_unacknowledged" in finding.message
        for finding in result.findings
    )


def test_trace_edge_rejects_relation_with_semantically_invalid_endpoints():
    claim = _node(NodeType.CLAIM, "CLAIM-INVALID-EDGE-001", 101)
    objective = _node(NodeType.OBJ, "OBJ-INVALID-EDGE-001", 102)

    with pytest.raises(
        TraceGraphError,
        match=r"CLAIM --supports--> OBJ",
    ):
        TraceEdge(
            claim.identity,
            EdgeType.SUPPORTS,
            objective.identity,
        )


def test_closure_rejects_short_promotion_path_that_skips_canonical_chain():
    objective = _node(NodeType.OBJ, "OBJ-SHORT-PATH-001", 11)
    analysis = _node(NodeType.ANL, "ANL-SHORT-PATH-001", 12)
    decision = _node(NodeType.DEC, "DEC-SHORT-PATH-001", 13)
    promotion = _node(NodeType.PROMOTION, "PROMOTION-SHORT-PATH-001", 14)
    graph = TraceGraph(
        nodes=(objective, analysis, decision, promotion),
        edges=(
            TraceEdge(
                analysis.identity,
                EdgeType.DERIVED_FROM,
                objective.identity,
            ),
            TraceEdge(
                decision.identity,
                EdgeType.DERIVED_FROM,
                analysis.identity,
            ),
            TraceEdge(
                promotion.identity,
                EdgeType.PROMOTES,
                decision.identity,
            ),
        ),
    )

    result = graph.validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert not result.ok
    assert any(
        finding.rule is ClosureRule.NODE_REACHES_OBJECTIVE
        and finding.node == promotion.identity
        for finding in result.findings
    )


def test_requirement_without_test_blocks_unless_exact_signed_waiver_is_current():
    objective = _node(NodeType.OBJ, "OBJ-WAIVER-001", 20)
    requirement = _node(NodeType.REQ, "REQ-WAIVER-001", 21)
    edge = TraceEdge(
        requirement.identity,
        EdgeType.IMPLEMENTS,
        objective.identity,
    )
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    key = b"trace-001-test-signing-key"

    blocked = TraceGraph(
        nodes=(objective, requirement),
        edges=(edge,),
    ).validate_closure(now=now, waiver_keys={"reviewer@example.com": key})

    assert not blocked.ok
    assert [finding.rule for finding in blocked.findings] == [
        ClosureRule.REQ_HAS_TEST
    ]

    waiver = TraceWaiver.sign(
        rule=ClosureRule.REQ_HAS_TEST,
        node=requirement.identity,
        reason="External acceptance test is temporarily unavailable.",
        signed_by="reviewer@example.com",
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        signing_key=key,
    )
    waived_graph = TraceGraph(
        nodes=(objective, requirement),
        edges=(edge,),
        waivers=(waiver,),
    )

    accepted = waived_graph.validate_closure(
        now=now,
        waiver_keys={"reviewer@example.com": key},
    )
    expired = waived_graph.validate_closure(
        now=waiver.expires_at,
        waiver_keys={"reviewer@example.com": key},
    )
    wrong_signature = waived_graph.validate_closure(
        now=now,
        waiver_keys={"reviewer@example.com": b"wrong-key"},
    )
    other_requirement = _node(NodeType.REQ, "REQ-WAIVER-OTHER", 22)
    wrong_node_waiver = TraceWaiver.sign(
        rule=ClosureRule.REQ_HAS_TEST,
        node=other_requirement.identity,
        reason="This waiver belongs to a different requirement.",
        signed_by="reviewer@example.com",
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        signing_key=key,
    )
    wrong_rule_waiver = TraceWaiver.sign(
        rule=ClosureRule.NODE_REACHES_OBJECTIVE,
        node=requirement.identity,
        reason="This waiver belongs to a different closure rule.",
        signed_by="reviewer@example.com",
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        signing_key=key,
    )
    wrong_scope = TraceGraph(
        nodes=(objective, requirement),
        edges=(edge,),
        waivers=(wrong_node_waiver, wrong_rule_waiver),
    ).validate_closure(
        now=now,
        waiver_keys={"reviewer@example.com": key},
    )

    assert accepted.ok, accepted.to_dict()
    assert accepted.waivers_used == (waiver,)
    assert not expired.ok
    assert expired.findings[0].rule is ClosureRule.REQ_HAS_TEST
    assert not wrong_signature.ok
    assert not wrong_scope.ok


def test_test_requires_runtime_evidence_and_a_pinned_run():
    graph, nodes = _closed_graph()
    evidence = nodes[NodeType.ART]
    run = nodes[NodeType.RUN]
    non_runtime_evidence = replace(evidence, runtime_evidence=False)
    unpinned_run = replace(run, pinned=False)

    no_runtime_result = TraceGraph(
        nodes=(
            non_runtime_evidence
            if node.identity == evidence.identity
            else node
            for node in graph.nodes
        ),
        edges=graph.edges,
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    unpinned_result = TraceGraph(
        nodes=(
            unpinned_run
            if node.identity == run.identity
            else node
            for node in graph.nodes
        ),
        edges=graph.edges,
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert ClosureRule.TEST_HAS_RUNTIME_EVIDENCE in {
        finding.rule for finding in no_runtime_result.findings
    }
    assert ClosureRule.TEST_HAS_PINNED_RUN in {
        finding.rule for finding in unpinned_result.findings
    }


def test_runtime_evidence_must_descend_from_the_same_pinned_run():
    graph, nodes = _closed_graph()
    test = nodes[NodeType.TEST]
    assignment = nodes[NodeType.ASN]
    pinned_run = nodes[NodeType.RUN]
    evidence = nodes[NodeType.ART]
    unpinned_run = _node(
        NodeType.RUN,
        "RUN-HARNESS-UNPINNED-001",
        15,
    )
    split_run_edges = tuple(
        edge
        for edge in graph.edges
        if not (
            edge.source == evidence.identity
            and edge.relation is EdgeType.DERIVED_FROM
            and edge.target == pinned_run.identity
        )
    ) + (
        TraceEdge(
            unpinned_run.identity,
            EdgeType.ASSIGNED_BY,
            assignment.identity,
        ),
        TraceEdge(
            evidence.identity,
            EdgeType.DERIVED_FROM,
            unpinned_run.identity,
        ),
    )

    result = TraceGraph(
        nodes=(*graph.nodes, unpinned_run),
        edges=split_run_edges,
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert not result.ok
    assert any(
        finding.rule is ClosureRule.TEST_HAS_PINNED_RUN
        and finding.node == test.identity
        for finding in result.findings
    )


def test_promotion_path_rejects_unpinned_run_despite_pinned_decoy_support():
    graph, nodes = _closed_graph()
    actual_run = nodes[NodeType.RUN]
    assignment = nodes[NodeType.ASN]
    actual_unpinned_run = replace(actual_run, pinned=False)
    decoy_run = _node(
        NodeType.RUN,
        "RUN-HARNESS-DECOY-PINNED-001",
        16,
        pinned=True,
    )
    decoy_artifact = _node(
        NodeType.ART,
        "ART-HARNESS-DECOY-PINNED-001",
        17,
        runtime_evidence=True,
    )
    decoy_grade = _node(
        NodeType.GRADE,
        "GRADE-HARNESS-DECOY-PINNED-001",
        18,
        verifier_id="VERIFIER-HARNESS-DECOY-001",
        verifier_revision_hash=canonical_revision_hash(
            {"verifier": "decoy", "version": "1"}
        ),
    )
    graph_with_decoy = TraceGraph(
        nodes=(
            *(
                actual_unpinned_run
                if node.identity == actual_run.identity
                else node
                for node in graph.nodes
            ),
            decoy_run,
            decoy_artifact,
            decoy_grade,
        ),
        edges=(
            *graph.edges,
            TraceEdge(
                decoy_run.identity,
                EdgeType.ASSIGNED_BY,
                assignment.identity,
            ),
            TraceEdge(
                decoy_artifact.identity,
                EdgeType.DERIVED_FROM,
                decoy_run.identity,
            ),
            TraceEdge(
                decoy_grade.identity,
                EdgeType.EVALUATES,
                decoy_artifact.identity,
            ),
        ),
    )

    result = graph_with_decoy.validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    promotion = nodes[NodeType.PROMOTION]
    promoted_path = graph_with_decoy.promotion_trace(promotion.identity)

    assert next(
        node for node in promoted_path if node.identity.node_type is NodeType.RUN
    ).pinned is False
    assert any(
        finding.rule.value == "promotion_path_has_pinned_run"
        and finding.node == promotion.identity
        for finding in result.findings
    )


def test_runtime_evidence_requires_a_grade_with_a_pinned_verifier():
    graph, nodes = _closed_graph()
    grade = nodes[NodeType.GRADE]
    no_grade_edge = tuple(
        edge
        for edge in graph.edges
        if not (
            edge.source == grade.identity
            and edge.relation is EdgeType.EVALUATES
        )
    )
    unverified_grade = replace(
        grade,
        verifier_id=None,
        verifier_revision_hash=None,
    )

    missing_grade_result = TraceGraph(
        nodes=graph.nodes,
        edges=no_grade_edge,
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    missing_verifier_result = TraceGraph(
        nodes=(
            unverified_grade
            if node.identity == grade.identity
            else node
            for node in graph.nodes
        ),
        edges=graph.edges,
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert ClosureRule.EVIDENCE_HAS_VERIFIER_GRADE in {
        finding.rule for finding in missing_grade_result.findings
    }
    assert {
        ClosureRule.EVIDENCE_HAS_VERIFIER_GRADE,
        ClosureRule.GRADE_HAS_VERIFIER,
    } <= {finding.rule for finding in missing_verifier_result.findings}


def test_decision_requires_analysis_and_promotion_requires_decision():
    graph, nodes = _closed_graph()
    decision = nodes[NodeType.DEC]
    promotion = nodes[NodeType.PROMOTION]

    without_analysis = TraceGraph(
        nodes=graph.nodes,
        edges=tuple(
            edge
            for edge in graph.edges
            if not (
                edge.source == decision.identity
                and edge.relation is EdgeType.DERIVED_FROM
            )
        ),
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    without_decision = TraceGraph(
        nodes=graph.nodes,
        edges=tuple(
            edge
            for edge in graph.edges
            if not (
                edge.source == promotion.identity
                and edge.relation is EdgeType.PROMOTES
            )
        ),
    ).validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert ClosureRule.DECISION_HAS_ANALYSIS in {
        finding.rule for finding in without_analysis.findings
    }
    assert ClosureRule.PROMOTION_HAS_DECISION in {
        finding.rule for finding in without_decision.findings
    }


def test_uncovered_node_blocks_and_validation_requires_explicit_aware_now():
    graph, _ = _closed_graph()
    orphan = _node(NodeType.CLAIM, "CLAIM-ORPHAN-001", 30)
    graph_with_orphan = TraceGraph(
        nodes=(*graph.nodes, orphan),
        edges=graph.edges,
    )

    result = graph_with_orphan.validate_closure(
        now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert ClosureRule.NODE_REACHES_OBJECTIVE in {
        finding.rule for finding in result.findings
    }
    with pytest.raises(TraceGraphError, match="explicit aware now"):
        graph.validate_closure(now=datetime(2026, 7, 12, 12, 0))


def test_planning_validator_blocks_on_trace_closure_and_accepts_closed_graph():
    closed_graph, nodes = _closed_graph()
    binding = _binding()
    closed_graph = _bind_decision(closed_graph, nodes, binding)
    requirement = nodes[NodeType.REQ]
    broken_graph = TraceGraph(
        nodes=closed_graph.nodes,
        edges=tuple(
            edge
            for edge in closed_graph.edges
            if not (
                edge.target == requirement.identity
                and edge.relation is EdgeType.TESTS
            )
        ),
        decision_grade_validator=closed_graph.decision_grade_validator,
    )
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    blocked = validate_planning_artifacts(
        (),
        gate="execution",
        trace_graph=broken_graph,
        trace_now=now,
        trace_binding=binding,
    )
    accepted = validate_planning_artifacts(
        (),
        gate="execution",
        trace_graph=closed_graph,
        trace_now=now,
        trace_binding=binding,
    )

    assert not blocked.ok
    assert blocked.checks["TRACE-001"].status == "fail"
    assert blocked.checks["TRACE-001"].details["findings"]
    assert accepted.ok, accepted.to_event_payload(task_id="trace-001")
    assert accepted.checks["TRACE-001"].status == "pass"


def test_planning_validator_blocks_when_required_trace_graph_is_missing():
    result = validate_planning_artifacts(
        (),
        gate="execution",
        trace_closure_required=True,
        trace_now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )

    assert not result.ok
    assert result.checks["TRACE-001"].status == "fail"
    assert result.checks["TRACE-001"].message == (
        "trace closure is required but no trace graph was supplied"
    )


def test_planning_validator_blocks_when_required_trace_graph_is_empty():
    binding = _binding()
    result = validate_planning_artifacts(
        (),
        gate="execution",
        trace_closure_required=True,
        trace_graph=TraceGraph(nodes=()),
        trace_now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        trace_binding=binding,
    )

    assert not result.ok
    assert result.checks["TRACE-001"].status == "fail"
    assert result.checks["TRACE-001"].message == (
        "trace closure validation failed: trace graph has no nodes"
    )


def test_planning_closure_rejects_superseded_grade_through_injected_interface():
    gradebook, old_grade = _gradebook_revision(passed=False, version="1.0")
    _gradebook_revision(
        gradebook=gradebook,
        supersedes_grade_id=old_grade.grade_id,
        passed=True,
        version="2.0",
    )
    graph, nodes = _closed_graph(
        gradebook=gradebook,
        grade_revision=old_grade,
    )
    binding = _binding()
    graph = _bind_decision(
        graph,
        nodes,
        binding,
        grade_citations=[
            {
                "grade_id": old_grade.grade_id,
                "revision_hash": old_grade.revision_hash,
                "acknowledged_invalidation_hashes": [],
            }
        ],
    )

    result = validate_planning_artifacts(
        (),
        gate="execution",
        trace_closure_required=True,
        trace_graph=graph,
        trace_now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        trace_binding=binding,
        trace_decision_grade_validator=gradebook,
    )

    assert not result.ok
    trace_check = result.checks["TRACE-001"]
    assert trace_check.status == "fail"
    assert any(
        finding["rule"] == "decision_grade_citations_current"
        and "stale_grade_unacknowledged" in finding["message"]
        for finding in trace_check.details["findings"]
    )


def test_planning_closure_rehashes_artifacts_and_rejects_stale_graph_binding(
    tmp_path: Path,
):
    plan_path = tmp_path / "implementation-plan.md"
    plan_path.write_text("original plan\n", encoding="utf-8")
    artifact = PlanningArtifact(
        path=plan_path,
        kind="implementation_plan",
    )
    binding = build_trace_closure_binding(
        task_id="trace-001-graph-20260711",
        run_id="trace-001-run",
        gate="execution",
        planning_artifacts=(artifact,),
    )
    graph, nodes = _closed_graph()
    graph = _bind_decision(graph, nodes, binding)
    plan_path.write_text("mutated plan\n", encoding="utf-8")

    result = validate_planning_artifacts(
        (artifact,),
        gate="execution",
        trace_graph=graph,
        trace_now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        trace_binding=binding,
    )

    assert not result.ok
    trace_check = result.checks["TRACE-001"]
    assert trace_check.status == "fail"
    assert trace_check.message == (
        "trace closure binding differs from the current gate or planning "
        "artifact refs/hashes"
    )


def test_trace_graph_store_round_trips_byte_equivalent_graph_and_closure(
    tmp_path: Path,
):
    objective = _node(NodeType.OBJ, "OBJ-STORE-001", 501)
    requirement = _node(NodeType.REQ, "REQ-STORE-001", 502)
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    signing_key = b"trace-store-waiver-key"
    waiver = TraceWaiver.sign(
        rule=ClosureRule.REQ_HAS_TEST,
        node=requirement.identity,
        reason="The durable-store fixture intentionally omits a test node.",
        signed_by="reviewer@example.com",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
        signing_key=signing_key,
    )
    graph = TraceGraph(
        nodes=(objective, requirement),
        edges=(
            TraceEdge(
                requirement.identity,
                EdgeType.IMPLEMENTS,
                objective.identity,
            ),
        ),
        waivers=(waiver,),
    )
    database = tmp_path / "trace.db"

    with TraceGraphStore(database) as store:
        store.append(graph)
        store.append(graph)

    with TraceGraphStore(database) as reopened:
        reloaded = reopened.load()

    result = reloaded.validate_closure(
        now=now,
        waiver_keys={"reviewer@example.com": signing_key},
    )

    assert reloaded.canonical_bytes() == graph.canonical_bytes()
    assert result.ok, result.to_dict()
    assert result.waivers_used == (waiver,)


def test_trace_graph_store_rejects_update_and_delete(tmp_path: Path):
    graph, _ = _closed_graph()
    database = tmp_path / "trace.db"

    with TraceGraphStore(database) as store:
        store.append(graph)

    statements = (
        "UPDATE trace_nodes SET payload_json = payload_json",
        "DELETE FROM trace_nodes",
        "UPDATE trace_edges SET payload_json = payload_json",
        "DELETE FROM trace_edges",
    )
    with sqlite3.connect(database) as connection:
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement)
            connection.rollback()
