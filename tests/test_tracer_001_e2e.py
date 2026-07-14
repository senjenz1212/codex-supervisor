from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from supervisor.claim_gate import ClaimGate, ClaimLevel, UnsupportedClaimError
from supervisor.evidence_committer import EVIDENCE_COMMIT_EVENT_KIND
from supervisor.evidence_ledger import (
    ContentAddressedArtifactStore,
    canonical_json_bytes,
)
from supervisor.experiment_kernel import Arm
from supervisor.grade_revisions import GradeBook
from supervisor.harness_tracer import (
    run_hermetic_harness_tracer,
    run_hermetic_treatment_wire_cut,
)
from supervisor.run_registry import load_session_registration
from supervisor.trace_graph import EdgeType, NodeType, TraceGraphStore


@pytest.mark.asyncio
async def test_hermetic_tracer_closes_the_full_matrix_and_refuses_l2_and_above(
    tmp_path: Path,
) -> None:
    report = await run_hermetic_harness_tracer(tmp_path / "tracer")

    expected_coordinates = {
        (task_family, runtime_kind, arm)
        for task_family in ("generic", "unity")
        for runtime_kind in ("claude_code", "codex")
        for arm in Arm
    }
    observed_coordinates = {
        (
            execution.coordinate.task_family,
            execution.coordinate.runtime_kind,
            execution.coordinate.arm,
        )
        for execution in report.executions
    }

    assert report.mode == "hermetic"
    assert report.operational_efficacy_evidence is False
    assert report.external_provider_calls == 0
    assert report.not_executed == (
        "Claude Code CLI",
        "Codex CLI",
        "SWE-bench",
        "Unity Test Framework",
    )
    assert len(report.executions) == 12
    assert observed_coordinates == expected_coordinates
    assert len({execution.execution_id for execution in report.executions}) == 12

    workspaces = [execution.transport.workspace for execution in report.executions]
    assert len(set(workspaces)) == 12
    assert all(
        execution.transport.marker_existed_before is False
        for execution in report.executions
    )
    assert all(
        execution.transport.hidden_read_blocked
        for execution in report.executions
    )
    assert all(
        execution.transport.network_used is False
        for execution in report.executions
    )
    assert all(
        execution.transport.external_process_started is False
        for execution in report.executions
    )
    assert all(execution.workspace_removed for execution in report.executions)
    assert all(not workspace.exists() for workspace in workspaces)

    for execution in report.executions:
        initial_receipt, recheck_receipt = execution.verifier_receipts

        assert execution.outcome.status == "completed"
        assert execution.outcome.cost_usd == 0.0
        assert execution.outcome.original_frozen_result_hash == (
            execution.arm_execution.frozen_result.result_hash
        )
        assert execution.outcome.blinded_frozen_result_hash == (
            execution.blinded_result.result_hash
        )
        assert execution.outcome.original_frozen_result_hash != (
            execution.outcome.blinded_frozen_result_hash
        )
        assert set(execution.outcome.blinding_removed_paths) == {
            "metadata.assignment_id",
            "metadata.evaluation_scope",
            "metadata.experiment",
            "metadata.harness_arm",
            "metadata.reserved_from",
            "metadata.tracer_mode",
            "output",
        }
        assert execution.outcome.grade.frozen_result_hash == (
            execution.blinded_result.result_hash
        )
        assert execution.blinded_result.schema_version == (
            "supervisor-frozen-task-result/v1"
        )

        for receipt in (initial_receipt, recheck_receipt):
            assert receipt.workspace_absent_at_verification
            assert receipt.treatment_blind
            assert receipt.hidden_content_absent
            assert receipt.hidden_fixture_present
            assert receipt.frozen_result == execution.blinded_result
            assert receipt.grade.frozen_result_hash == (
                execution.blinded_result.result_hash
            )
        execution_receipt = execution.arm_execution.receipt
        assert execution_receipt is not None
        assert execution.transport.treatment_hash == (
            execution_receipt.treatment_hash
        )
        assert execution.transport.arm_adapter == {
            Arm.A: "production-baseline",
            Arm.B: "supervisor-orchestration",
            Arm.C: "compute-matched-direct",
        }[execution.coordinate.arm]
        assert execution.transport.entrypoint == {
            Arm.A: "baseline.execute",
            Arm.B: "supervisor.execute",
            Arm.C: "direct.execute",
        }[execution.coordinate.arm]

        revisions = execution.grade_history.revisions
        invalidations = execution.grade_history.invalidations
        assert [revision.revision_number for revision in revisions] == [1, 2]
        assert revisions[0].revision_hash != revisions[1].revision_hash
        assert revisions[1].supersedes_grade_id == revisions[0].grade_id
        assert revisions[1].verifier_implementation_hash == (
            execution.task_spec.verifier_hash
        )
        assert len(invalidations) == 1
        assert invalidations[0].kind == "superseded"
        assert invalidations[0].replacement_grade_id == revisions[1].grade_id
        terminal_commits = execution.grade_history.terminal_commits
        assert [commit.grade_id for commit in terminal_commits] == [
            revision.grade_id for revision in revisions
        ]
        assert [
            commit.grade_revision_hash for commit in terminal_commits
        ] == [revision.revision_hash for revision in revisions]
        assert all(
            commit.experiment_id == execution.experiment_id
            and commit.task_id == execution.task_spec.task_id
            and commit.arm == execution.coordinate.arm.value
            and commit.terminal_state
            == execution.grade_history.terminal_state
            and commit.terminal_state_hash
            == execution.grade_history.terminal_state_hash
            for commit in terminal_commits
        )
        assert execution.grade_history.terminal_state == "completed"
        source_terminal_commits = (
            execution.grade_history.source_terminal_commits
        )
        assert len(source_terminal_commits) == 1
        source_terminal_commit = source_terminal_commits[0]
        assert execution.outcome.grade_revision is not None
        assert source_terminal_commit.grade_id == (
            execution.outcome.grade_revision.grade_id
        )
        assert source_terminal_commit.grade_revision_hash == (
            execution.outcome.grade_revision.revision_hash
        )
        assert (
            source_terminal_commit.experiment_id,
            source_terminal_commit.task_id,
            source_terminal_commit.arm,
            source_terminal_commit.terminal_state,
            source_terminal_commit.terminal_state_hash,
        ) == (
            execution.experiment_id,
            execution.task_spec.task_id,
            execution.coordinate.arm.value,
            execution.grade_history.terminal_state,
            execution.grade_history.terminal_state_hash,
        )

    assert len(report.registered_run_ids) == 13
    assert report.aggregate_run_id in report.registered_run_ids
    assert set(report.ledger_verifications) == set(report.registered_run_ids)
    assert all(
        verification.valid
        and verification.truncation_checked
        and verification.authoritative_head_verified
        for verification in report.ledger_verifications.values()
    )
    assert report.evidence_commit_status == "complete"
    assert report.evidence_commit_phases == (
        "initialized",
        "grades_verified",
        "trace_persisted",
        "artifacts_staged",
        "manifest_appended",
        "checkpoints_persisted",
        "authoritatively_verified",
        "complete",
    )
    assert len(report.checkpoint_refs) == 13
    assert all(
        Path(unquote(urlparse(reference).path)).is_file()
        for reference in report.checkpoint_refs.values()
    )

    manifest_events = [
        event
        for event in report.aggregate_events
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]
    assert len(manifest_events) == 1
    assert manifest_events[0]["event_id"] == report.manifest_event_id
    assert (
        manifest_events[0]["payload"]["artifact_manifest_hash"]
        == report.artifact_manifest["manifest_hash"]
    )
    assert (
        manifest_events[0]["artifact_manifest_hash"]
        == report.artifact_manifest["manifest_hash"]
    )

    artifact_roles = {
        item["role"]
        for item in report.artifact_manifest["metadata"]["artifact_roles"]
    }
    assert {
        "canonical_run_references",
        "canonical_result_references",
        "claim_evidence_bundle",
        "claim_report",
        "execution_results",
        "experiment_snapshot",
        "grade_revisions",
        "gradebook_snapshot",
        "hidden_verifier_result",
        "run_manifest",
        "state_snapshot",
        "trace_graph",
        "trace_store_snapshot",
        "tracer_projection",
    } <= artifact_roles

    artifact_store = ContentAddressedArtifactStore(
        report.evidence_root / "cas"
    )
    assert artifact_store.verify_manifest(report.artifact_manifest)
    grade_history_descriptor = next(
        artifact
        for artifact in report.artifact_manifest["artifacts"]
        if artifact["name"] == "artifacts/grade-revisions.json"
    )
    grade_history_evidence = json.loads(
        artifact_store.read_bytes(
            grade_history_descriptor["digest"]["sha256"]
        )
    )
    published_histories = {
        history["execution_id"]: history
        for history in grade_history_evidence["histories"]
    }
    assert set(published_histories) == {
        execution.execution_id for execution in report.executions
    }
    for execution in report.executions:
        assert [
            commit["commit_hash"]
            for commit in published_histories[
                execution.execution_id
            ]["terminal_commits"]
        ] == [
            commit.commit_hash
            for commit in execution.grade_history.terminal_commits
        ]
        assert [
            commit["commit_hash"]
            for commit in published_histories[
                execution.execution_id
            ]["source_terminal_commits"]
        ] == [
            commit.commit_hash
            for commit in execution.grade_history.source_terminal_commits
        ]
    projection_descriptor = next(
        artifact
        for artifact in report.artifact_manifest["artifacts"]
        if artifact["name"] == "artifacts/tracer-projection.json"
    )
    projection_bytes = artifact_store.read_bytes(
        projection_descriptor["digest"]["sha256"]
    )
    assert projection_bytes == canonical_json_bytes(report.projection)
    assert report.projection_sha256 == projection_descriptor["digest"]["sha256"]
    executions_descriptor = next(
        artifact
        for artifact in report.artifact_manifest["artifacts"]
        if artifact["name"] == "artifacts/executions.json"
    )
    execution_evidence = json.loads(
        artifact_store.read_bytes(
            executions_descriptor["digest"]["sha256"]
        )
    )
    execution_records = {
        record["execution_id"]: record
        for record in execution_evidence["executions"]
    }
    assert set(execution_records) == {
        execution.execution_id for execution in report.executions
    }
    for execution in report.executions:
        record = execution_records[execution.execution_id]
        assert record["terminal_state"] == "completed"
        assert (
            record["terminal_state_hash"]
            == execution.grade_history.terminal_state_hash
        )
        assert [
            commit["commit_hash"]
            for commit in record["grade_terminal_commits"]
        ] == [
            commit.commit_hash
            for commit in execution.grade_history.terminal_commits
        ]
        assert [
            commit["commit_hash"]
            for commit in record["source_grade_terminal_commits"]
        ] == [
            commit.commit_hash
            for commit in execution.grade_history.source_terminal_commits
        ]
    assert report.projection["recognized_event_count"] == 21
    assert len(report.projection["matrix"]) == 12
    assert len(report.projection["assignments"]) == 4
    assert len(report.projection["executions"]) == 12
    assert report.projection["claim"]["max_claim_level"] == "L1"
    assert report.projection["completion"] == {
        "execution_count": 12,
        "claim_cap": "L1",
        "mode": "hermetic",
        "external_provider_calls": 0,
    }

    with TraceGraphStore(report.trace_store_path) as trace_store:
        persisted_trace = trace_store.load()
    assert persisted_trace.canonical_bytes() == report.trace_graph.canonical_bytes()

    joined_events = [
        event
        for event in report.aggregate_events
        if event["kind"] == "tracer.execution.joined"
    ]
    assert len(joined_events) == 12
    assert {
        event["payload"]["execution_id"] for event in joined_events
    } == {execution.execution_id for execution in report.executions}
    assert all(event["payload"]["runtime_run_id"] for event in joined_events)
    assert all(event["payload"]["runtime_session_id"] for event in joined_events)
    assert all(
        event["payload"]["session_id_source"]
        == "hermetic_runtime_receipt"
        for event in joined_events
    )
    assert all(
        Path(event["payload"]["session_registration_ref"]).parent
        == report.run_registry_path
        for event in joined_events
    )
    assert all(event["payload"]["grade_revision_hash"] for event in joined_events)
    assert all(
        len(event["payload"]["grade_revision_hashes"]) == 2
        for event in joined_events
    )
    assert all(
        len(event["payload"]["grade_invalidation_hashes"]) == 1
        for event in joined_events
    )
    assert all(
        event["payload"]["terminal_state"] == "completed"
        and len(event["payload"]["terminal_state_hash"]) == 64
        and len(event["payload"]["grade_terminal_commit_hashes"]) == 2
        for event in joined_events
    )
    for execution in report.executions:
        joined = next(
            event
            for event in joined_events
            if event["payload"]["execution_id"] == execution.execution_id
        )
        assert (
            joined["payload"]["terminal_state_hash"]
            == execution.grade_history.terminal_state_hash
        )
        assert joined["payload"]["grade_terminal_commit_hashes"] == [
            commit.commit_hash
            for commit in execution.grade_history.terminal_commits
        ]
        registration = load_session_registration(
            report.run_registry_path,
            execution.transport.session_id,
        )
        assert registration is not None
        assert registration["workflow_run_id"] == execution.transport.run_id
        assert (
            registration["target_session_id"]
            == execution.transport.session_id
        )

    assert report.trace_closure.ok, report.trace_closure.to_dict()
    assert sum(
        node.identity.node_type is NodeType.RUN
        for node in report.trace_graph.nodes
    ) == 12
    assert sum(
        node.identity.node_type is NodeType.ART
        for node in report.trace_graph.nodes
    ) == 12
    assert sum(
        node.identity.node_type is NodeType.GRADE
        for node in report.trace_graph.nodes
    ) == 36
    assert sum(
        node.attributes.get("record_kind") == "grade_revision"
        for node in report.trace_graph.nodes
    ) == 24
    assert sum(
        node.attributes.get("record_kind") == "grade_invalidation"
        for node in report.trace_graph.nodes
    ) == 12
    assert sum(
        edge.relation is EdgeType.SUPERSEDES
        for edge in report.trace_graph.edges
    ) == 12
    assert sum(
        edge.relation is EdgeType.INVALIDATES
        for edge in report.trace_graph.edges
    ) == 12
    decision = next(
        node
        for node in report.trace_graph.nodes
        if node.identity.node_type is NodeType.DEC
    )
    citations = decision.attributes["grade_citations"]
    assert len(citations) == 12
    assert {
        citation["grade_id"] for citation in citations
    } == {
        execution.grade_history.revisions[-1].grade_id
        for execution in report.executions
    }
    assert [node.identity.node_type for node in report.promotion_trace] == [
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

    verifier_evidence = report.claim_evidence_bundle[
        "independent_hidden_verifier"
    ]
    assert verifier_evidence["independent"] is False
    assert (
        verifier_evidence["producer_principal_id"]
        == verifier_evidence["verifier_principal_id"]
    )
    assert report.claim_level is ClaimLevel.L1
    assert report.claim_report["claim_gate"]["max_claim_level"] == "L1"
    assert report.claim_report["improvement_claim_allowed"] is False
    assert report.claim_report["powered_improvement_claim_allowed"] is False
    assert report.l2_refusal == (
        "asserted claim level L2 exceeds evidence support L1"
    )
    assert report.l3_refusal == (
        "asserted claim level L3 exceeds evidence support L1"
    )
    with pytest.raises(
        UnsupportedClaimError,
        match="asserted claim level L2 exceeds evidence support L1",
    ):
        ClaimGate.validate_report(
            {"asserted_claim_level": "L2"},
            report.claim_evidence_bundle,
            evidence_root=report.evidence_root,
        )

    for task_family in ("generic", "unity"):
        for runtime_kind in ("claude_code", "codex"):
            scenario = {
                execution.coordinate.arm: execution
                for execution in report.executions
                if execution.coordinate.task_family == task_family
                and execution.coordinate.runtime_kind == runtime_kind
            }
            treatment_hashes = {
                execution.arm_execution.receipt.treatment_hash
                for execution in scenario.values()
                if execution.arm_execution.receipt is not None
            }
            assert len(treatment_hashes) == 3
            assert (
                scenario[Arm.B].arm_execution.receipt.compute_resource_hash
                == scenario[Arm.C].arm_execution.receipt.compute_resource_hash
            )


@pytest.mark.asyncio
async def test_hermetic_tracer_blocks_when_a_grade_terminal_commit_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_commit = GradeBook.commit_terminal_grade

    def omit_tracer_grade_commit(
        self: GradeBook,
        **kwargs: object,
    ) -> None:
        if Path(self.path).name == "grades.db":
            return None
        real_commit(self, **kwargs)

    monkeypatch.setattr(
        GradeBook,
        "commit_terminal_grade",
        omit_tracer_grade_commit,
    )

    with pytest.raises(
        RuntimeError,
        match="grade terminal authority is incomplete",
    ):
        await run_hermetic_harness_tracer(
            tmp_path / "missing-terminal-commit"
        )


@pytest.mark.asyncio
async def test_disabling_supervisor_orchestration_breaks_b_but_not_c(
    tmp_path: Path,
) -> None:
    report = await run_hermetic_treatment_wire_cut(
        tmp_path / "treatment-wire-cut"
    )

    assert report.b_failed is True
    assert "supervisor orchestration wire is disabled" in report.b_failure
    assert report.c_completed is True
    assert len(report.c_treatment_hash) == 64
    assert report.supervisor_orchestration_calls == 1
    assert report.adapter_invocations == {
        "production-baseline": 0,
        "supervisor-orchestration": 1,
        "compute-matched-direct": 1,
    }
