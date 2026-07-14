from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from mcp_tools.codex_supervisor_stdio import _maybe_artifact
import supervisor.dual_agent_artifacts as dual_agent_artifacts_module
from supervisor.dual_agent_artifacts import (
    ScreenshotArtifact,
    _file_tree_sha256,
    export_dual_agent_run_artifacts,
    verify_dual_agent_export,
)
from supervisor.evidence_committer import HmacCheckpointAuthority
from supervisor.evidence_ledger import (
    canonical_json_bytes,
    strict_json_object_loads,
    verify_event_chain,
)
from supervisor.ledger_checkpoints import (
    FilesystemTrustedCheckpointPinStore,
    LedgerCheckpointCoordinator,
    LedgerCheckpointPolicy,
    LedgerCheckpointStore,
)
from supervisor.production_trace import (
    ProductionTraceEvidence,
    ProductionTraceRecorder,
)
from supervisor.redaction import redact
from supervisor.replay_versions import check_replay_schema_versions
from supervisor.review_packets import ChangedFile, build_review_packet
from supervisor.state import State
from supervisor.trace_graph import TraceClosureBinding, TracePlanningArtifactRef


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


def _state(tmp_path: Path) -> State:
    return State(str(tmp_path / "state.db"))


def _authoritative_state(tmp_path: Path) -> State:
    authority = HmacCheckpointAuthority(
        key_id="artifact-export-test-key",
        key=b"artifact-export-test-key-material",
    )
    coordinator = LedgerCheckpointCoordinator(
        signer=authority,
        verifier=authority,
        checkpoint_store=LedgerCheckpointStore(tmp_path / "checkpoints"),
        trusted_pin_store=FilesystemTrustedCheckpointPinStore(
            tmp_path / "trusted-pins"
        ),
        policy=LedgerCheckpointPolicy(max_events_between_checkpoints=1),
    )
    return State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=coordinator,
    )


def _insert_event(
    state: State,
    *,
    run_id: str = "run-1",
    kind: str,
    payload: dict,
    ts: int = 1000,
) -> int:
    return state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind=kind,
        payload=payload,
        ts=ts,
    )


def _insert_review_packet(
    state: State,
    *,
    task_id: str,
    gate: str,
    changed_files: list[ChangedFile],
    ts: int = 999,
) -> int:
    packet = build_review_packet(
        task_id=task_id,
        run_id="run-1",
        gate=gate,
        packet_id=f"review-packet-{gate}-1",
        base_head="a" * 40,
        candidate_head="b" * 40,
        changed_files=changed_files,
    )
    return _insert_event(
        state,
        kind="supervisor_review_packet_created",
        payload={
            "schema_version": "supervisor-review-packet/v1",
            **packet.to_event_payload(),
            "validation": {"status": "passed", "failures": []},
        },
        ts=ts,
    )


def _round_payload(
    *,
    task_id: str = "task-1",
    gate: str,
    round_index: int,
    codex_decision: str,
    claude_decision: str,
    objection: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "gate": gate,
        "round": {
            "round_index": round_index,
            "codex_decision": codex_decision,
            "claude_decision": claude_decision,
            "codex_confidence": 0.9,
            "claude_confidence": 0.8,
            "objection": objection,
        },
    }


def _result_payload(
    *,
    task_id: str = "task-1",
    gate: str,
    status: str = "accepted",
    summary: str,
    decisions: list[str],
    objections: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "gate": gate,
        "status": status,
        "attempts": 1,
        "handoff_packet_path": f"/tmp/.handoff/{task_id}.json",
        "probes": {
            "P1": {"probe_id": "P1", "status": "green", "reason": "planning_artifact_boundaries_ok", "details": {}},
            "P2": {"probe_id": "P2", "status": "green", "reason": "worker_orchestration_invocation_ok", "details": {}},
            "P3": {"probe_id": "P3", "status": "green", "reason": "outcome_fidelity_ok", "details": {}},
        },
        "outcome": {
            "task_id": task_id,
            "summary": summary,
            "specialists": [{"name": "Reviewer", "decision": decisions[0], "objection": None}],
            "decisions": decisions,
            "objections": objections or [],
            "changed_files": ["supervisor/example.py"],
            "tests": ["uv run pytest tests/test_example.py"],
            "test_status": "passed",
            "confidence": 0.91,
            "claims": ["Claim one"],
        },
        "escalation": None,
    }


def _production_trace_source_payload(
    repo: Path,
    *,
    task_id: str = "task-1",
    gate: str = "execution",
    identity: str = "canonical",
    workspace_root: Path | None = None,
) -> dict:
    planning_artifact = TracePlanningArtifactRef(
        kind="implementation_plan",
        path=f"/repo/docs/{identity}-implementation-plan.md",
        sha256=sha256(f"{identity}-canonical-plan".encode()).hexdigest(),
    )
    runtime_result_hash = sha256(
        f"{identity}-canonical-runtime-result".encode()
    ).hexdigest()
    runtime_session_id = f"session-{identity}"
    runtime_run_id = f"runtime-{identity}"
    payload = _result_payload(
        task_id=task_id,
        gate=gate,
        summary=f"{gate} accepted with canonical production semantics.",
        decisions=["accept"],
    )
    payload.update({
        "tool_calls": [{
            "name": "start_dual_agent_gate",
            "runtime": "codex",
            "runtime_run_id": runtime_run_id,
            "runtime_session_id": runtime_session_id,
            "runtime_result_hash": runtime_result_hash,
            "model": "gpt-test",
        }],
        "target_run_registrations": [{
            "target_run_id": f"assignment-{identity}",
            "target_session_id": runtime_session_id,
            "runtime_run_id": runtime_run_id,
            "runtime_result_hash": runtime_result_hash,
        }],
        "trace_closure_binding": TraceClosureBinding(
            task_id=task_id,
            run_id="run-1",
            gate=gate,
            planning_artifacts=(planning_artifact,),
        ).to_dict(),
        "production_trace_workspace_root": str(
            (workspace_root or repo).resolve()
        ),
    })
    return payload


def _record_canonical_production_trace(
    state: State,
    *,
    repo: Path,
    source_event_id: int,
    task_id: str = "task-1",
    gate: str | None = None,
):
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    source_payload = strict_json_object_loads(
        str(source_event["payload_json"])
    )
    planning_binding = TraceClosureBinding.from_mapping(
        source_payload["trace_closure_binding"]
    )
    assert planning_binding.task_id == task_id
    assert planning_binding.run_id == "run-1"
    if gate is not None:
        assert planning_binding.gate == gate
    runtime_calls = [
        dict(call)
        for call in source_payload["tool_calls"]
        if str(call.get("runtime_session_id") or "").strip()
    ]
    assert runtime_calls
    runtime_call = runtime_calls[-1]
    runtime_session_id = str(runtime_call["runtime_session_id"])
    registrations = [
        dict(registration)
        for registration in source_payload[
            "target_run_registrations"
        ]
        if registration["target_session_id"] == runtime_session_id
    ]
    assert len(registrations) == 1
    runtime_registration = registrations[0]
    runtime_provenance = {
        "assignment_id": runtime_registration["target_run_id"],
        "arm": "supervisor",
        "runtime_kind": runtime_call.get("runtime"),
        "runtime_run_id": runtime_call["runtime_run_id"],
        "runtime_session_id": runtime_session_id,
        "runtime_result_hash": runtime_call["runtime_result_hash"],
        "model": runtime_call.get("model"),
        "attempts": int(source_payload["attempts"]),
        "runtime_calls": runtime_calls,
        "target_run_registrations": list(
            source_payload["target_run_registrations"]
        ),
    }
    run_envelope_hash = _test_payload_sha256({
        "schema_version": "supervisor-production-run-envelope/v1",
        "task_id": planning_binding.task_id,
        "run_id": planning_binding.run_id,
        "gate": planning_binding.gate,
        "source_event_hash": source_event_hash,
        "runtime_provenance": runtime_provenance,
    })
    runtime_provenance["run_envelope_hash"] = run_envelope_hash
    frozen_result_hash = _test_payload_sha256(source_payload)
    task_hash = _test_payload_sha256({
        "task_id": planning_binding.task_id,
        "run_id": planning_binding.run_id,
        "gate": planning_binding.gate,
        "planning_artifacts": [
            artifact.to_dict()
            for artifact in planning_binding.planning_artifacts
        ],
    })
    gate_hash = _test_payload_sha256(planning_binding.to_dict())
    workspace_root = Path(
        source_payload["production_trace_workspace_root"]
    )
    assert workspace_root == repo.resolve()
    trace_root = (
        workspace_root
        / ".codex-supervisor"
        / "production-traces"
        / source_event_hash
    )
    receipt = ProductionTraceRecorder(
        trace_store_path=trace_root / "trace.db",
        gradebook_path=trace_root / "grades.db",
    ).record(ProductionTraceEvidence(
        task_id=planning_binding.task_id,
        task_hash=task_hash,
        run_id=planning_binding.run_id,
        run_envelope_hash=run_envelope_hash,
        frozen_result_hash=frozen_result_hash,
        gate=planning_binding.gate,
        gate_hash=gate_hash,
        planning_artifacts=planning_binding.planning_artifacts,
        runtime_provenance=runtime_provenance,
        result_provenance={
            "frozen_result_hash": frozen_result_hash,
            "result_kind": "dual_agent_gate_result",
            "result_receipt_hash": source_event_hash,
            "public_result": redact(source_payload),
        },
        source_event_id=str(source_event_id),
        source_event_hash=source_event_hash,
        source_event_state="completed",
        source_event_recorded_at_ms=int(source_event["ts"]) * 1000,
        final_gate_result={
            "status": str(source_payload["status"]),
            "gate_result_hash": frozen_result_hash,
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "result": redact(source_payload),
        },
    ))
    return receipt, source_event_hash


def _record_semantically_substituted_production_trace(
    state: State,
    *,
    repo: Path,
    source_event_id: int,
    task_id: str = "task-1",
    gate: str = "execution",
    identity: str = "default",
):
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    run_envelope_hash = sha256(
        f"{identity}-run-envelope".encode()
    ).hexdigest()
    frozen_result_hash = sha256(
        f"{identity}-frozen-result".encode()
    ).hexdigest()
    trace_root = (
        repo
        / ".codex-supervisor"
        / "production-traces"
        / source_event_hash
    )
    receipt = ProductionTraceRecorder(
        trace_store_path=trace_root / "trace.db",
        gradebook_path=trace_root / "grades.db",
    ).record(ProductionTraceEvidence(
        task_id=task_id,
        task_hash=sha256(f"{identity}-task".encode()).hexdigest(),
        run_id="run-1",
        run_envelope_hash=run_envelope_hash,
        frozen_result_hash=frozen_result_hash,
        gate=gate,
        gate_hash=sha256(f"{identity}-gate".encode()).hexdigest(),
        planning_artifacts=(
            TracePlanningArtifactRef(
                kind="implementation_plan",
                path="/repo/docs/implementation-plan.md",
                sha256=sha256(f"{identity}-plan".encode()).hexdigest(),
            ),
        ),
        runtime_provenance={
            "assignment_id": f"assignment-{identity}",
            "arm": "supervisor",
            "runtime_kind": "codex",
            "runtime_run_id": f"runtime-{identity}",
            "run_envelope_hash": run_envelope_hash,
        },
        result_provenance={
            "frozen_result_hash": frozen_result_hash,
            "result_kind": "dual_agent_gate_result",
            "result_receipt_hash": source_event_hash,
        },
        source_event_id=str(source_event_id),
        source_event_hash=source_event_hash,
        source_event_state="completed",
        source_event_recorded_at_ms=int(source_event["ts"]) * 1000,
        final_gate_result={
            "status": "accepted",
            "gate_result_hash": sha256(
                f"{identity}-gate-result".encode()
            ).hexdigest(),
        },
    ))
    return receipt, source_event_hash


def _test_payload_sha256(payload: dict) -> str:
    return sha256(json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")).hexdigest()


def _export_valid_production_trace_package(
    tmp_path: Path,
    *,
    gates: tuple[str, ...] = ("execution",),
):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_ids: list[int] = []
    recorded_event_ids: list[int] = []
    receipts = []
    for index, gate in enumerate(gates):
        source_event_id = _insert_event(
            state,
            kind="dual_agent_gate_result",
            payload=_production_trace_source_payload(
                repo,
                gate=gate,
                identity=f"package-{index}",
            ),
            ts=1_784_000_000 + (index * 2),
        )
        receipt, source_event_hash = _record_canonical_production_trace(
            state,
            repo=repo,
            source_event_id=source_event_id,
            gate=gate,
        )
        recorded_event_id = _insert_event(
            state,
            kind="dual_agent_production_trace_recorded",
            payload={
                "task_id": "task-1",
                "gate": gate,
                "status": "recorded",
                "source_event_id": source_event_id,
                "source_event_hash": source_event_hash,
                "receipt": receipt.to_dict(),
            },
            ts=1_784_000_001 + (index * 2),
        )
        source_event_ids.append(source_event_id)
        recorded_event_ids.append(recorded_event_id)
        receipts.append(receipt)
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )
    assert result.status == "ok"
    return (
        state,
        result,
        tuple(source_event_ids),
        tuple(recorded_event_ids),
        tuple(receipts),
    )


def _recommit_export_integrity(package_dir: Path) -> str:
    integrity_path = package_dir / "replay" / "export-integrity.json"
    integrity = strict_json_object_loads(
        integrity_path.read_text(encoding="utf-8")
    )
    for descriptor in integrity["files"]:
        content = (package_dir / descriptor["path"]).read_bytes()
        descriptor["size"] = len(content)
        descriptor["sha256"] = sha256(content).hexdigest()
    integrity["file_tree_sha256"] = sha256(canonical_json_bytes({
        "schema_version": "dual-agent-public-export-file-tree/v1",
        "files": integrity["files"],
    })).hexdigest()
    root_preimage = dict(integrity)
    root_preimage.pop("export_root_sha256")
    integrity["export_root_sha256"] = sha256(
        canonical_json_bytes(root_preimage)
    ).hexdigest()
    integrity_path.write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return integrity["export_root_sha256"]


def test_export_dual_agent_run_artifacts_writes_readable_gate_documents(tmp_path):
    state = _state(tmp_path)
    prd_round = _insert_event(
        state,
        kind="dual_agent_gate_round",
        payload=_round_payload(
            gate="prd_review",
            round_index=1,
            codex_decision="revise",
            claude_decision="revise",
            objection="Acceptance criteria missing.",
        ),
    )
    prd_result = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted after tightening.",
            decisions=["accept"],
        ),
        ts=1001,
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="tdd_review",
            summary="TDD plan accepted.",
            decisions=["accept"],
            objections=[],
        ),
        ts=1002,
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Outcome accepted.",
            decisions=["accept"],
            objections=[],
        ),
        ts=1003,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    assert result.status == "ok"
    assert result.output_dir == tmp_path / "docs" / "dual-agent" / "task-1"
    assert [path.name for path in result.files] == [
        "index.md",
        "triage.md",
        "prd.md",
        "tdd.md",
        "grill-findings.md",
        "issues.md",
        "screenshots.md",
        "outcome-review.md",
        "interactions.md",
        "transcript.md",
        "transcript.jsonl",
        "mast-coverage.md",
        "manifest.json",
        "workspace-snapshot.json",
        "mast-coverage.json",
        "evidence-ledger.jsonl",
        "export-integrity.json",
    ]
    assert "PRD accepted after tightening." in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_round}" in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_result}" in (result.output_dir / "prd.md").read_text()
    assert "Acceptance criteria missing." in (result.output_dir / "grill-findings.md").read_text()
    assert "No issue artifacts were recorded in the dual-agent ledger." in (result.output_dir / "issues.md").read_text()
    assert "No screenshot artifacts were supplied for this export." in (result.output_dir / "screenshots.md").read_text()
    assert "Outcome accepted." in (result.output_dir / "outcome-review.md").read_text()
    interactions = (result.output_dir / "interactions.md").read_text()
    assert "# Agent Interactions: task-1" in interactions
    assert "## 1. PRD Review" in interactions
    assert "Codex -> Claude Code" in interactions
    assert "Claude Code -> Codex" in interactions
    assert "Codex decision: `revise`" in interactions
    assert "Claude decision: `revise`" in interactions
    assert "Acceptance criteria missing." in interactions
    assert "Outcome summary: PRD accepted after tightening." in interactions
    assert "## 4. Outcome Review" in interactions
    assert "Outcome summary: Outcome accepted." in interactions
    assert "prd_review" in (result.output_dir / "transcript.md").read_text()
    transcript_jsonl = (result.output_dir / "transcript.jsonl").read_text()
    assert '"event_id": ' in transcript_jsonl
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["run_id"] == "run-1"
    assert manifest["task_id"] == "task-1"
    assert manifest["events_count"] == 4
    assert manifest["files"]["transcript_jsonl"] == "transcript.jsonl"


def test_export_replaces_unbound_worker_authored_outcome_review(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="execution",
            summary="Execution report accepted.",
            decisions=["accept"],
        ),
        ts=1001,
    )
    output_dir = tmp_path / "docs" / "dual-agent" / "task-1"
    output_dir.mkdir(parents=True)
    report = output_dir / "outcome-review.md"
    report.write_text("# Production Confidence\n\nWorker-authored report.\n", encoding="utf-8")

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )

    assert result.status == "ok"
    assert report.read_text(encoding="utf-8") != (
        "# Production Confidence\n\nWorker-authored report.\n"
    )
    assert "No events recorded for this gate." in report.read_text(
        encoding="utf-8"
    )


def test_export_preserves_hash_bound_worker_authored_outcome_review(tmp_path):
    state = _state(tmp_path)
    output_dir = tmp_path / "docs" / "dual-agent" / "task-1"
    output_dir.mkdir(parents=True)
    report = output_dir / "outcome-review.md"
    report.write_text(
        "# Production Confidence\n\nWorker-authored report.\n",
        encoding="utf-8",
    )
    _insert_review_packet(
        state,
        task_id="task-1",
        gate="outcome_review",
        changed_files=[
            ChangedFile(
                path="docs/dual-agent/task-1/outcome-review.md",
                status="M",
                sha256=sha256(report.read_bytes()).hexdigest(),
            )
        ],
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Outcome gate log should stay in transcript.",
            decisions=["accept"],
        ),
        ts=1001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
        trusted_workspace_root=tmp_path,
    )

    assert result.status == "ok"
    assert report.read_text(encoding="utf-8") == "# Production Confidence\n\nWorker-authored report.\n"
    assert "Outcome gate log should stay in transcript." in (
        result.output_dir / "transcript.md"
    ).read_text(encoding="utf-8")


def test_export_preserves_only_hash_bound_nested_deliverables(tmp_path):
    state = _state(tmp_path)
    output_dir = tmp_path / "docs" / "dual-agent" / "task-1"
    report = output_dir / "pilot" / "report.json"
    stale = output_dir / "pilot" / "stale.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status":"measured"}\n', encoding="utf-8")
    stale.write_text('{"status":"old"}\n', encoding="utf-8")
    review_event_id = _insert_review_packet(
        state,
        task_id="task-1",
        gate="outcome_review",
        changed_files=[
            ChangedFile(
                path="docs/dual-agent/task-1/pilot/report.json",
                status="A",
                sha256=sha256(report.read_bytes()).hexdigest(),
            )
        ],
    )
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            tmp_path,
            gate="outcome_review",
            identity="nested-deliverable",
        ),
        ts=1001,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=tmp_path,
        source_event_id=source_event_id,
        gate="outcome_review",
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1002,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
        trusted_workspace_root=tmp_path,
    )

    assert report.read_text(encoding="utf-8") == '{"status":"measured"}\n'
    assert not stale.exists()
    manifest = strict_json_object_loads(
        (output_dir / "replay" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["preserved_artifacts"] == [
        {
            "path": "pilot/report.json",
            "sha256": sha256(report.read_bytes()).hexdigest(),
            "source_event_id": review_event_id,
            "source_kind": "supervisor_review_packet_created",
            "source_path": "docs/dual-agent/task-1/pilot/report.json",
        }
    ]
    verification = verify_dual_agent_export(
        output_dir,
        expected_root=result.export_root_sha256,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert verification.valid, verification.issues

    manifest["preserved_artifacts"][0]["source_event_id"] += 1
    (output_dir / "replay" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(output_dir)
    forged = verify_dual_agent_export(
        output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert not forged.valid
    assert any(
        "preserved artifact ledger binding" in issue
        for issue in forged.issues
    )


def test_export_rejects_hash_bound_deliverable_mismatch(tmp_path):
    state = _state(tmp_path)
    output_dir = tmp_path / "docs" / "dual-agent" / "task-1"
    report = output_dir / "pilot" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status":"mutated"}\n', encoding="utf-8")
    _insert_review_packet(
        state,
        task_id="task-1",
        gate="outcome_review",
        changed_files=[
            ChangedFile(
                path="docs/dual-agent/task-1/pilot/report.json",
                status="A",
                sha256=sha256(b'{"status":"accepted"}\n').hexdigest(),
            )
        ],
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Pilot report accepted.",
            decisions=["accept"],
        ),
        ts=1001,
    )

    with pytest.raises(ValueError, match="hash-bound export artifact"):
        export_dual_agent_run_artifacts(
            state,
            run_id="run-1",
            task_id="task-1",
            output_dir=output_dir,
            trusted_workspace_root=tmp_path,
        )


def test_explicit_trusted_root_cannot_be_expanded_by_ledger_snapshot(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    proof = outside / "artifacts" / "proof.txt"
    proof.parent.mkdir(parents=True)
    proof.write_text("outside authority\n", encoding="utf-8")
    _insert_event(
        state,
        kind="dual_agent_dynamic_workflow_manifest",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "accepted",
            "artifact_path": str(proof),
            "artifact_sha256": sha256(proof.read_bytes()).hexdigest(),
        },
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="execution",
                summary="Ledger snapshot must not widen caller authority.",
                decisions=["accept"],
            ),
            "acceptance_evidence": {
                "handoff_packet": {},
                "workspace_snapshot": {
                    "status": "captured",
                    "root": str(outside),
                },
            },
        },
        ts=1001,
    )
    output_dir = repo / "docs" / "dual-agent" / "task-1"

    export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
        trusted_workspace_root=repo,
    )

    assert not (output_dir / "artifacts" / "proof.txt").exists()
    manifest = strict_json_object_loads(
        (output_dir / "replay" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["preserved_artifacts"] == []


def test_hash_bound_export_rejects_ancestor_symlink_swap_during_open(
    tmp_path,
    monkeypatch,
):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    artifact_dir = repo / "artifacts"
    artifact_dir.mkdir(parents=True)
    proof = artifact_dir / "proof.txt"
    proof.write_text("untrusted original\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_proof = outside / "proof.txt"
    outside_proof.write_text("ledger-matching escape\n", encoding="utf-8")
    _insert_event(
        state,
        kind="dual_agent_dynamic_workflow_manifest",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "accepted",
            "artifact_path": str(proof),
            "artifact_sha256": sha256(outside_proof.read_bytes()).hexdigest(),
        },
    )
    output_dir = repo / "docs" / "dual-agent" / "task-1"
    real_open = os.open
    swapped = False

    def swap_parent_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (
            not swapped
            and (
                str(path) == str(proof)
                or (str(path) == proof.name and dir_fd is not None)
            )
        ):
            swapped = True
            original = repo / "artifacts-original"
            artifact_dir.rename(original)
            artifact_dir.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_parent_then_open)

    with pytest.raises(ValueError, match="hash-bound export artifact"):
        export_dual_agent_run_artifacts(
            state,
            run_id="run-1",
            task_id="task-1",
            output_dir=output_dir,
            trusted_workspace_root=repo,
        )

    assert swapped is True
    assert not (output_dir / "artifacts" / "proof.txt").exists()


def test_export_dual_agent_run_artifacts_renders_interaction_receipts(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "schema_version": "dual-agent-interaction/v1",
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "codex",
            "recipient": "cursor",
            "message_type": "review_request",
            "content": "Challenge the receipt coverage.",
            "round_index": 1,
            "persona_id": "codex.lifecycle_reviewer",
            "addresses": ["event:12"],
            "confidence": {
                "value": 0.83,
                "source": "deterministic_policy",
                "criteria": ["receipt_required"],
                "rationale": "Codex needs Cursor to review missing push evidence.",
                "evidence": ["receipt:test:passed"],
            },
            "claims": ["tests passed"],
            "objections": ["push receipt missing"],
            "questions": ["Does the push receipt map to this commit?"],
            "tool_receipts": [{
                "receipt_id": "pytest-focused",
                "kind": "test",
                "status": "passed",
                "command": "uv run pytest -q",
            }],
            "evidence_refs": [{
                "kind": "pytest",
                "ref": "receipt:pytest-focused",
                "status": "passed",
            }],
            "raw_transcript_refs": [{
                "kind": "claude_stdout",
                "ref": ".handoff/task-1.stdout",
                "sha256": "abc123",
            }],
            "would_change_if": "A matching git_remote receipt appears.",
            "critical_review": {
                "schema_version": "critical-review/v1",
                "strongest_objection": "push receipt missing",
                "missing_evidence": ["git_remote receipt"],
                "contradictions_checked": ["reported tests vs receipts"],
                "assumptions_to_verify": ["branch was pushed"],
                "what_would_change_my_mind": "A matching git_remote receipt appears.",
                "decision": "revise",
                "severity": "important",
            },
            "artifacts": [],
            "metadata": {
                "tool_calls": [
                    {"name": "start_dual_agent_gate", "status": "completed"},
                ],
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_interaction_message",
                "policy_verdict": "observed",
                "failure_taxonomy": {
                    "schema_version": "dual-agent-failure-taxonomy/v1",
                    "framework": "MAST-inspired",
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "code": "workflow_claim_verification_failed",
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                    "mast_category": "Task Verification",
                },
                "tool_calls": [
                    {
                        "name": "start_dual_agent_gate",
                        "status": "completed",
                        "started_at_ms": 1000,
                        "ended_at_ms": 1035,
                        "duration_ms": 35,
                    },
                ],
                "artifacts": [],
                "claims": ["tests passed"],
                "receipts": [],
            },
        },
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `review_request`" in text
        assert "persona_id: `codex.lifecycle_reviewer`" in text
        assert "tests passed" in text
        assert "push receipt missing" in text
        assert "Does the push receipt map to this commit?" in text
        assert "pytest-focused" in text
        assert "receipt:pytest-focused" in text
        assert ".handoff/task-1.stdout" in text
        assert "A matching git_remote receipt appears." in text
        assert "### Critical Review" in text
        assert "push receipt missing" in text
        assert "reported tests vs receipts" in text
        assert "start_dual_agent_gate" in text
        assert "FM-3.2" in text
        assert "No or incomplete verification" in text
        assert "duration_ms" in text


def test_export_dual_agent_run_artifacts_renders_cursor_review_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "tdd_review",
            "cursor_review": {
                "accepted": False,
                "probe": {
                    "probe_id": "CURSOR",
                    "status": "red",
                    "reason": "cursor_review_failed",
                    "details": {"missing": ["receipt:git-diff"]},
                },
                "outcome": {
                    "task_id": "task-1",
                    "summary": "Cursor found missing diff evidence.",
                    "specialists": [{"name": "Cursor Reviewer", "decision": "revise"}],
                    "decisions": ["revise"],
                    "objections": ["diff receipt missing"],
                    "changed_files": [],
                    "tests": [],
                    "test_status": "unknown",
                    "confidence": 0.72,
                    "claims": ["receipt coverage incomplete"],
                },
                "agent_id": "cursor-agent-1",
                "run_id": "cursor-run-1",
                "status": "completed",
                "model": "composer-2.5",
                "duration_ms": 1234,
                "transcript_tail": "Cursor transcript tail.",
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `cursor_review`" in text
        assert "accepted: `False`" in text
        assert "Cursor found missing diff evidence." in text
        assert "receipt coverage incomplete" in text
        assert "diff receipt missing" in text
        assert "composer-2.5" in text
        assert "Cursor transcript tail." in text


def test_export_dual_agent_run_artifacts_renders_independent_reviewer_panel_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="independent_reviewer_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "independent_reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "accepted": True,
                    "decision": "accept",
                    "severity": "none",
                    "confidence": 0.91,
                    "runtime": "litellm_structured",
                    "model": "gemini-3.1-pro-preview",
                    "provider_family": "google",
                    "lineage": ["google", "litellm_structured", "gemini-3.1-pro-preview"],
                    "tool_access": "text_only",
                    "assurance_grade": "text_only",
                    "transcript_refs": [
                        {
                            "kind": "reviewer_transcript_tail",
                            "ref": "independent_reviewer_review:task-1:outcome_review:1:independent-reviewer-0",
                        }
                    ],
                    "transcript_sha256": "abc123",
                    "output_sha256": "def456",
                    "critical_review": {
                        "schema_version": "critical-review/v1",
                        "severity": "none",
                        "decision": "accept",
                    },
                }
            ],
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `independent_reviewer_review`" in text
        assert "reviewer_count: `1`" in text
        assert "independent-reviewer-0" in text
        assert "gemini-3.1-pro-preview" in text
        assert "provider_family: `google`" in text
        assert "tool_access: `text_only`" in text
        assert "assurance_grade: `text_only`" in text
        assert "transcript_sha256: `abc123`" in text
        assert "output_sha256: `def456`" in text


def test_export_dual_agent_run_artifacts_renders_top_level_cursor_review_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "accepted": True,
            "probe": {
                "probe_id": "CURSOR",
                "status": "green",
                "reason": "cursor_review_ok",
                "details": {},
            },
            "outcome": {
                "task_id": "task-1",
                "summary": "Cursor accepted fixture fidelity while noting missing receipts.",
                "specialists": [{"name": "Cursor Reviewer", "decision": "accept"}],
                "decisions": ["accept"],
                "objections": [],
                "changed_files": [],
                "tests": [],
                "test_status": "unknown",
                "confidence": 0.92,
                "claims": ["implementation and test claims unsubstantiated in worktree"],
            },
            "agent_id": "cursor-agent-live",
            "cursor_run_id": "cursor-run-live",
            "status": "completed",
            "model": "composer-2.5",
            "duration_ms": 11701,
            "transcript_tail": "Top-level Cursor transcript tail.",
            "raw_transcript_refs": [
                {
                    "kind": "cursor_transcript_fixture",
                    "ref": "tests/fixtures/dual_agent/cursor-transcript.txt",
                },
            ],
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "tri_agent_cursor_review",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [
                    {
                        "name": "invoke_cursor_agent",
                        "status": "completed",
                        "requested_model": "composer-2.5",
                        "result_summary": {"probe_id": "CURSOR"},
                    },
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    transcript_jsonl = (result.output_dir / "transcript.jsonl").read_text()
    for text in (interactions, transcript):
        assert "accepted: `True`" in text
        assert "cursor-agent-live" in text
        assert "cursor-run-live" in text
        assert "composer-2.5" in text
        assert "11701" in text
        assert "Cursor accepted fixture fidelity while noting missing receipts." in text
        assert "implementation and test claims unsubstantiated in worktree" in text
        assert "Top-level Cursor transcript tail." in text
        assert "full_reasoning: `transcript.jsonl event 1 transcript_tail`" in text
        assert "full_reasoning_ref: `tests/fixtures/dual_agent/cursor-transcript.txt`" in text
        assert "invoke_cursor_agent" in text
        assert "CURSOR" in text
        assert "requested_model" in text
    assert '"cursor_run_id": "cursor-run-live"' in transcript_jsonl


def test_export_dual_agent_run_artifacts_renders_not_invoked_gate_without_blank_claude_section(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "claude_gate_status": "not_invoked",
            "supervisor_final_status": "blocked",
            "attempts": 0,
            "handoff_packet_path": None,
            "probes": {},
            "outcome": None,
            "escalation": {
                "type": "artifact_rigor",
                "reason": "required_artifacts_missing",
            },
            "artifact_rigor": {
                "status": "blocked",
                "reason": "required_artifacts_missing",
                "missing_artifacts": ["prd"],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    assert "### Supervisor Block" in interactions
    assert "Claude Code was not invoked." in interactions
    assert "required_artifacts_missing" in interactions
    assert "Outcome summary: None recorded." not in interactions


def test_export_dual_agent_run_artifacts_renders_cursor_failure_reason_when_outcome_missing(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "cursor_review": {
                "accepted": False,
                "probe": {
                    "probe_id": "CURSOR",
                    "status": "red",
                    "reason": "cursor_invocation_failed",
                    "details": {"error": "missing_api_key"},
                },
                "outcome": None,
                "agent_id": None,
                "run_id": None,
                "status": None,
                "model": None,
                "duration_ms": None,
                "transcript_tail": "",
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    assert "No typed Cursor outcome parsed." in interactions
    assert "### Cursor Failure" in interactions
    assert "- reason: `cursor_invocation_failed`" in interactions
    assert "missing_api_key" in interactions


def test_export_dual_agent_run_artifacts_renders_planning_validation_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_planning_validation",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "validator_version": "1.0.0",
            "artifact_hashes": {"prd": "a" * 64},
            "checks": {
                "PRD-001": "pass",
                "TDD-001": "fail: missing test names",
            },
            "verdict": "blocked",
            "artifacts": [
                {
                    "kind": "prd",
                    "path": "/tmp/prd.md",
                    "sha256": "a" * 64,
                    "status": "accepted",
                },
            ],
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_planning_validation",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "category": "system_design",
                    "subcategory": "invalid_or_missing_artifact",
                    "code": "planning_validation_failed",
                },
                "tool_calls": [
                    {"name": "validate_planning_artifacts", "status": "red"},
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `planning_validation`" in text
        assert "validator_version: `1.0.0`" in text
        assert "verdict: `blocked`" in text
        assert "TDD-001: fail: missing test names" in text
        assert "/tmp/prd.md" in text
        assert "validate_planning_artifacts" in text


def test_export_dual_agent_run_artifacts_renders_dynamic_workflow_receipt_validation(tmp_path):
    state = _state(tmp_path)
    state.write_event(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_dynamic_workflow_receipt_validation",
        payload={
            "task_id": "task-1",
            "gate": "workflow_start",
            "status": "blocked",
            "probe": {
                "probe_id": "P13",
                "status": "red",
                "reason": "missing_dynamic_workflow_receipts",
                "details": {
                    "dynamic_workflow_task_class": "codebase_audit",
                    "required_gates": [
                        "codex_and_lead_remain_supervision_layer",
                        "per_subagent_budget_caps_verified",
                    ],
                    "verified_gates": [],
                    "missing_gates": [
                        "codex_and_lead_remain_supervision_layer",
                        "per_subagent_budget_caps_verified",
                    ],
                    "receipt_ids": [],
                },
            },
            "tool_calls": [
                {"name": "verify_dynamic_workflow_receipts", "status": "red"},
            ],
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `dynamic_workflow_receipt_validation`" in text
        assert "P13 Dynamic Workflow Receipt Validation" in text
        assert "missing_dynamic_workflow_receipts" in text
        assert "verify_dynamic_workflow_receipts" in text


def test_export_dual_agent_run_artifacts_links_tdd_grill_source_artifact(tmp_path):
    state = _state(tmp_path)
    state.write_event(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Accepted.",
            decisions=["accept"],
        ),
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    index = (result.output_dir / "index.md").read_text()
    assert "Source PRD Grill Findings" in index
    assert "source/grill-findings.md" in index
    assert "Source TDD Grill Findings" in index
    assert "source/grill-findings-tdd.md" in index


def test_export_dual_agent_run_artifacts_writes_replay_manifest_with_handoff_content(tmp_path):
    state = _state(tmp_path)
    handoff = tmp_path / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text('{"task_id": "task-1", "gate": "outcome_review"}\n', encoding="utf-8")
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())

    assert manifest["event_ids"] == [event_id]
    assert manifest["handoff_packets"][0]["path"] == str(handoff)
    assert manifest["handoff_packets"][0]["status"] == "captured"
    assert manifest["handoff_packets"][0]["content"] == handoff.read_text(encoding="utf-8")
    assert len(manifest["handoff_packets"][0]["sha256"]) == 64
    assert check_replay_schema_versions(manifest)["status"] == "compatible"
    [manifest_event] = [
        event
        for event in state.read_events_since("run-1", after_event_id=0, limit=100)
        if event["kind"] == "dual_agent_replay_manifest_recorded"
    ]
    assert manifest_event["payload"]["run_id"] == "run-1"
    assert manifest_event["payload"]["task_id"] == "task-1"
    assert manifest_event["payload"]["manifest_sha256"] == sha256(
        (result.output_dir / "replay" / "manifest.json").read_bytes()
    ).hexdigest()
    integrity = strict_json_object_loads(
        (result.output_dir / "replay" / "export-integrity.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest_event["payload"]["export_root_sha256"] == (
        result.export_root_sha256
    )
    assert manifest_event["payload"]["file_tree_sha256"] == integrity[
        "file_tree_sha256"
    ]
    assert manifest_event["payload"]["ledger_head_event_hash"] == manifest[
        "ledger"
    ]["head_event_hash"]


def test_explicit_trusted_root_blocks_outside_posthoc_handoff_reads(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    handoff = outside / "task-1.json"
    handoff.write_text(
        json.dumps({
            "task_id": "task-1",
            "cwd": str(outside),
            "secret": "must-not-be-exported",
        })
        + "\n",
        encoding="utf-8",
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outside handoff must remain outside caller authority.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        trusted_workspace_root=repo,
    )

    manifest_text = (
        result.output_dir / "replay" / "manifest.json"
    ).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["handoff_packets"][0]["status"] == (
        "outside_trusted_workspace"
    )
    assert manifest["handoff_packets"][0]["content"] is None
    assert manifest["workspace_snapshot"]["status"] == (
        "acceptance_snapshot_invalid"
    )
    assert "must-not-be-exported" not in manifest_text


def test_explicit_trusted_root_blocks_outside_acceptance_snapshot_ref(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    snapshot_path = outside / "acceptance.json"
    snapshot = {
        "schema_version": "dual-agent-acceptance-snapshot/v1",
        "handoff_packet": {},
        "workspace_snapshot": {
            "status": "captured",
            "root": str(outside),
            "secret": "must-not-be-exported",
        },
    }
    snapshot_bytes = json.dumps(snapshot, sort_keys=True).encode("utf-8")
    snapshot_path.write_bytes(snapshot_bytes)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outside acceptance snapshot is not caller-authorized.",
                decisions=["accept"],
            ),
            "acceptance_evidence": {
                "snapshot_ref": str(snapshot_path),
                "snapshot_sha256": sha256(snapshot_bytes).hexdigest(),
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        trusted_workspace_root=repo,
    )

    snapshot_manifest = json.loads(
        (
            result.output_dir
            / "replay"
            / "workspace-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    assert snapshot_manifest["status"] == "acceptance_snapshot_invalid"
    assert snapshot_manifest["snapshot_ref"] == str(snapshot_path)
    assert "must-not-be-exported" not in json.dumps(snapshot_manifest)


def test_replay_manifest_records_resolved_models_and_component_hashes(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "task_id": "task-1",
            "gate": "tdd_review",
            "message_type": "gate_request",
            "content": "Review the TDD plan against the recorded contract.",
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "tdd_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_interaction_message",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [
                    {
                        "name": "invoke_cursor_reviewer",
                        "args": {
                            "requested_model": "default",
                            "model_source": "quality_default:best",
                            "runtime": "cursor_sdk",
                            "cli_command": "cursor-agent",
                        },
                        "model": "composer-2.5",
                        "result_summary": {"model": "composer-2.5"},
                    },
                    {
                        "name": "evaluate_outcome_fidelity",
                        "args": {"probe_id": "P3"},
                        "result_summary": {"status": "green"},
                    },
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    [lane] = manifest["model_resolutions"]
    assert lane["requested_model"] == "default"
    assert lane["resolved_model"] == "composer-2.5"
    assert lane["resolution_source"] == "response_model"
    provenance = manifest["execution_provenance"]
    assert provenance["status"] == "incomplete"
    assert provenance["unresolved_model_lanes"] == []
    assert "containers" in provenance["missing_component_categories"]
    assert provenance["workspace_issues"]
    assert set(manifest["component_hashes"]) == {
        "prompts",
        "tool_contracts",
        "containers",
        "cli",
        "evaluators",
    }
    for components in manifest["component_hashes"].values():
        assert components
        assert all(
            len(component["sha256"]) == 64
            if component["details"]["status"] == "verified"
            else component["sha256"] == ""
            for component in components
        )


def test_export_dual_agent_run_artifacts_writes_workspace_snapshot_manifest(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    (repo / "README.md").write_text("seed\nchanged\n", encoding="utf-8")
    source = repo / "docs" / "dual-agent" / "task-1" / "source"
    source.mkdir(parents=True)
    prd = source / "prd.md"
    prd.write_text("# PRD\n\nreal artifact body\n", encoding="utf-8")
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text(
        json.dumps({
            "task_id": "task-1",
            "gate": "outcome_review",
            "cwd": str(repo),
            "planning_artifacts": [
                {
                    "kind": "prd",
                    "path": "docs/dual-agent/task-1/source/prd.md",
                    "sha256": sha256(prd.read_text(encoding="utf-8").encode()).hexdigest(),
                    "mutable_by_worker": False,
                },
            ],
        }) + "\n",
        encoding="utf-8",
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    snapshot_file = json.loads((result.output_dir / "replay" / "workspace-snapshot.json").read_text())
    snapshot = manifest["workspace_snapshot"]
    assert snapshot_file == snapshot
    assert snapshot["status"] == "captured"
    assert snapshot["root"] == str(repo)
    assert snapshot["git"]["head"]
    assert snapshot["git"]["head_sha"] == snapshot["git"]["head"]
    assert snapshot["git"]["head_ref"] == "HEAD"
    assert snapshot["git"]["head_label"] == "handoff_cwd_head"
    assert "README.md" in snapshot["git"]["status_short"]
    assert len(snapshot["git"]["diff_sha256"]) == 64
    assert len(snapshot["file_tree_sha256"]) == 64
    assert snapshot["source_artifact_hashes"]["prd"] == sha256(
        prd.read_text(encoding="utf-8").encode()
    ).hexdigest()


def test_workspace_snapshot_captures_hashed_immutable_overlay_for_dirty_tree(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True, text=True)
    recorded_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "README.md").write_text("historical result\n", encoding="utf-8")
    (repo / "new.txt").write_text("captured untracked file\n", encoding="utf-8")
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text(json.dumps({"cwd": str(repo), "planning_artifacts": []}))
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    snapshot = manifest["workspace_snapshot"]
    overlay = snapshot["immutable_snapshot"]
    assert snapshot["git"]["head_sha"] == recorded_head
    assert len(snapshot["git"]["head_sha"]) == 40
    assert overlay["schema_version"] == "dual-agent-workspace-overlay/v1"
    assert overlay["status"] == "captured"
    assert overlay["base_commit"] == recorded_head
    assert len(overlay["sha256"]) == 64
    assert {entry["path"] for entry in overlay["entries"]} >= {
        "README.md",
        "new.txt",
    }


def test_export_uses_acceptance_time_handoff_and_workspace_after_later_mutation(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("accepted bytes\n", encoding="utf-8")
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    accepted_handoff = json.dumps({
        "task_id": "task-1",
        "cwd": str(repo),
        "planning_artifacts": [],
    })
    handoff.write_text(accepted_handoff, encoding="utf-8")
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    (repo / "README.md").write_text("mutated after acceptance\n", encoding="utf-8")
    handoff.write_text(
        json.dumps({"task_id": "task-1", "cwd": str(tmp_path / "wrong-repo")}),
        encoding="utf-8",
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())

    assert manifest["handoff_packets"][0]["content"] == accepted_handoff
    snapshot = manifest["workspace_snapshot"]
    assert snapshot["capture_source"] == "accepted_gate_event"
    readme_entry = next(
        entry
        for entry in snapshot["immutable_snapshot"]["entries"]
        if entry["path"] == "README.md"
    )
    assert base64.b64decode(readme_entry["content_base64"]) == b"accepted bytes\n"


def test_release_grade_export_reports_incomplete_provenance(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Outcome accepted without replay provenance.",
            decisions=["accept"],
        ),
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
        require_complete_provenance=True,
    )

    assert result.status == "incomplete"
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["execution_provenance"]["status"] == "incomplete"


def test_release_grade_export_carries_runtime_receipts_to_complete_manifest(
    tmp_path,
):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff_content = json.dumps({
        "task_id": "task-1",
        "cwd": str(repo),
        "planning_artifacts": [],
    })
    handoff.write_text(handoff_content, encoding="utf-8")
    interaction_event_id = _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "message_type": "gate_request",
            "content": "Review the execution-time provenance.",
            "requested_model": "default",
            "model": "default",
            "runtime": "custom",
            "provider_family": "provider",
            "container_digest": "b" * 64,
            "trace_envelope": {
                "tool_calls": [
                    {
                        "name": "invoke_custom",
                        "args": {
                            "runtime": "custom",
                            "cli_command": "custom-cli",
                        },
                    },
                    {
                        "name": "verify_result",
                        "args": {},
                    },
                ],
            },
        },
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted with runtime receipts.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
            "acceptance_evidence": {
                "handoff_packet": {
                    "path": str(handoff),
                    "status": "captured",
                    "sha256": sha256(handoff_content.encode()).hexdigest(),
                    "content": handoff_content,
                },
                "workspace_snapshot": {
                    "status": "captured",
                    "capture_source": "accepted_gate_event",
                    "root": str(repo),
                    "git": {"head_sha": "a" * 40},
                    "file_tree_sha256": "d" * 64,
                    "immutable_snapshot": {
                        "status": "captured",
                        "sha256": "e" * 64,
                    },
                },
            },
        },
        ts=1001,
    )
    contract_bytes = {
        name: json.dumps(
            {"name": name, "inputSchema": {"type": "object"}},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for name in ("invoke_custom", "verify_result")
    }

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
        require_complete_provenance=True,
        provider_model_resolutions=[{
            "event_id": interaction_event_id,
            "gate": "outcome_review",
            "lane": "dual_agent_interaction_message",
            "runtime": "custom",
            "provider_family": "provider",
            "requested_model": "default",
            "resolved_model": "provider/model-v1-20260713",
            "provider_response_receipt_ref": (
                "receipt://provider-response/run-1/outcome-review"
            ),
        }],
        canonical_tool_contracts=[
            {
                "tool_name": name,
                "canonical_bytes": content,
                "sha256": sha256(content).hexdigest(),
                "receipt_ref": f"receipt://tool-contract/{name}",
                "capture_source": "execution_time",
                "source": "runtime_tool_registry",
            }
            for name, content in contract_bytes.items()
        ],
        runtime_component_receipts=[
            {
                "category": "containers",
                "component_id": "container:container_digest",
                "sha256": "b" * 64,
                "receipt_ref": "receipt://runtime-component/container/main",
                "capture_source": "execution_time",
                "source": "runtime_component_receipt",
            },
            {
                "category": "cli",
                "component_id": "cli:invoke_custom",
                "canonical_bytes": b"custom cli executable bytes",
                "sha256": sha256(
                    b"custom cli executable bytes"
                ).hexdigest(),
                "receipt_ref": "receipt://runtime-component/cli/custom",
                "capture_source": "execution_time",
                "source": "runtime_component_receipt",
            },
            {
                "category": "evaluators",
                "component_id": "evaluator:verify_result",
                "canonical_bytes": b"verify result evaluator bytes",
                "sha256": sha256(
                    b"verify result evaluator bytes"
                ).hexdigest(),
                "receipt_ref": (
                    "receipt://runtime-component/evaluator/verify-result"
                ),
                "capture_source": "execution_time",
                "source": "runtime_component_receipt",
            },
        ],
    )

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    provenance = manifest["execution_provenance"]
    assert result.status == "ok"
    assert provenance["status"] == "complete"
    assert provenance["missing_component_categories"] == []
    assert provenance["unresolved_model_lanes"] == []
    assert provenance["model_resolutions"][0][
        "provider_response_source"
    ].startswith("receipt://provider-response/")
    assert {
        component["details"]["receipt_ref"]
        for category in ("containers", "cli", "evaluators")
        for component in provenance["component_hashes"][category]
    } == {
        "receipt://runtime-component/container/main",
        "receipt://runtime-component/cli/custom",
        "receipt://runtime-component/evaluator/verify-result",
    }


def test_release_export_copies_reconstructable_production_trace_authority(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    gate_payload = _production_trace_source_payload(
        repo,
        identity="reconstructable",
    )
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=gate_payload,
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    interleaved_event_id = _insert_event(
        state,
        kind="unrelated_run_diagnostic",
        payload={
            "task_id": "task-2",
            "status": "observed",
        },
        ts=1_784_000_000,
    )
    receipt, _ = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    trace_export = manifest["production_trace"]
    assert result.status == "ok"
    assert trace_export["status"] == "complete"
    record = trace_export["records"][0]
    assert record["status"] == "complete"
    for artifact in record["public_artifacts"].values():
        exported = result.output_dir / artifact["path"]
        assert exported.is_file()
        assert sha256(exported.read_bytes()).hexdigest() == artifact["sha256"]

    clean_room = tmp_path / "clean-room-export"
    shutil.copytree(result.output_dir, clean_room)
    integrity_path = clean_room / "replay" / "export-integrity.json"
    integrity = strict_json_object_loads(
        integrity_path.read_text(encoding="utf-8")
    )
    assert result.export_root_sha256 == integrity["export_root_sha256"]
    assert len(result.export_root_sha256) == 64
    assert result.ledger_head_hash == integrity["ledger"]["head_event_hash"]
    assert integrity["ledger"] == manifest["ledger"]
    clean_room_verification = verify_dual_agent_export(
        clean_room,
        expected_root=result.export_root_sha256,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert clean_room_verification.valid
    assert clean_room_verification.issues == ()

    ledger_path = clean_room / integrity["ledger"]["path"]
    ledger_rows = [
        strict_json_object_loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sha256(ledger_path.read_bytes()).hexdigest() == integrity[
        "ledger"
    ]["sha256"]
    verification = verify_event_chain(
        ledger_rows,
        expected_run_id="run-1",
        expected_head_hash=integrity["ledger"]["head_event_hash"],
        expected_event_identity_hash=integrity["ledger"][
            "head_event_identity_hash"
        ],
    )
    assert verification.valid
    assert verification.event_count == integrity["ledger"]["event_count"]
    assert integrity["ledger"]["captured_head_event_id"] == (
        integrity["ledger"]["head_event_id"]
    )
    rows_by_id = {
        row["event_id"]: row
        for row in ledger_rows
    }
    assert rows_by_id[interleaved_event_id]["kind"] == (
        "unrelated_run_diagnostic"
    )
    assert record["binding_status"] == "verified"
    assert record["recorded_event"] == {
        "event_id": rows_by_id[record["event_id"]]["event_id"],
        "event_sequence": rows_by_id[record["event_id"]]["event_sequence"],
        "event_hash": rows_by_id[record["event_id"]]["event_hash"],
        "kind": "dual_agent_production_trace_recorded",
    }
    assert record["source_event"] == {
        "event_id": source_event_id,
        "event_sequence": rows_by_id[source_event_id]["event_sequence"],
        "event_hash": source_event_hash,
        "kind": "dual_agent_gate_result",
    }

    for descriptor in integrity["files"]:
        exported = clean_room / descriptor["path"]
        assert exported.is_file()
        assert len(exported.read_bytes()) == descriptor["size"]
        assert sha256(exported.read_bytes()).hexdigest() == descriptor["sha256"]
    assert [descriptor["path"] for descriptor in integrity["files"]] == [
        path.relative_to(clean_room).as_posix()
        for path in sorted(clean_room.rglob("*"))
        if path.is_file() and path != integrity_path
    ]
    file_tree = {
        "schema_version": "dual-agent-public-export-file-tree/v1",
        "files": integrity["files"],
    }
    assert sha256(canonical_json_bytes(file_tree)).hexdigest() == integrity[
        "file_tree_sha256"
    ]
    root_preimage = dict(integrity)
    root_preimage.pop("export_root_sha256")
    assert (
        sha256(canonical_json_bytes(root_preimage)).hexdigest()
        == result.export_root_sha256
    )


def test_production_trace_rejects_attacker_labeled_canonical_source(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = state.write_event(
        run_id="run-1",
        source="attacker",
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="attacker-source",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest = strict_json_object_loads(
        (result.output_dir / "replay" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    [record] = manifest["production_trace"]["records"]
    assert result.status == "incomplete"
    assert record["binding_status"] == "invalid"
    assert any(
        "canonical source event source is not dual_agent" in issue
        for issue in record["issues"]
    )

    record["status"] = "complete"
    record["binding_status"] = "verified"
    record["authority_status"] = "verified"
    record["issues"] = []
    manifest["production_trace"]["status"] = "complete"
    manifest["production_trace"]["issues"] = []
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert not verification.valid
    assert any(
        "canonical source event source is not dual_agent" in issue
        for issue in verification.issues
    )


def test_clean_room_rejects_recommitted_receipt_semantics_not_in_source(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="canonical-source",
            workspace_root=repo / "canonical-workspace",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = (
        _record_semantically_substituted_production_trace(
            state,
            repo=repo,
            source_event_id=source_event_id,
            identity="receipt-supplied-substitute",
        )
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    [record] = manifest["production_trace"]["records"]
    assert result.status == "incomplete"
    assert record["authority_status"] == "invalid"
    assert any(
        "persisted production trace evidence differs from the canonical "
        "gate payload" in issue
        for issue in record["issues"]
    )

    record["status"] = "complete"
    record["binding_status"] = "verified"
    record["authority_status"] = "verified"
    record["issues"] = []
    manifest["production_trace"]["status"] = "complete"
    manifest["production_trace"]["issues"] = []
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert not verification.valid
    assert any(
        "persisted production trace evidence differs from the canonical "
        "gate payload" in issue
        for issue in verification.issues
    )


def test_release_export_rejects_production_trace_ancestor_symlink_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="ancestor-symlink",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )

    trace_root = Path(receipt.trace_store_path).parent
    outside = tmp_path / "outside-trace"
    outside.mkdir()
    shutil.copyfile(receipt.trace_store_path, outside / "trace.db")
    shutil.copyfile(receipt.gradebook_path, outside / "grades.db")
    original = trace_root.with_name(trace_root.name + "-original")
    real_read = dual_agent_artifacts_module._read_regular_path_no_follow
    swapped = False

    def swap_ancestor_then_read(path, *, trusted_roots):
        nonlocal swapped
        if not swapped and Path(path).name == "trace.db":
            swapped = True
            trace_root.rename(original)
            trace_root.symlink_to(outside, target_is_directory=True)
        return real_read(path, trusted_roots=trusted_roots)

    monkeypatch.setattr(
        dual_agent_artifacts_module,
        "_read_regular_path_no_follow",
        swap_ancestor_then_read,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        trusted_workspace_root=repo,
    )

    manifest = strict_json_object_loads(
        (result.output_dir / "replay" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert swapped is True
    assert manifest["production_trace"]["status"] == "incomplete"
    assert any(
        "not a trusted regular file" in issue
        for issue in manifest["production_trace"]["issues"]
    )
    assert not list(
        (result.output_dir / "replay" / "production-traces").rglob("*.db")
    )


def test_release_export_rejects_tampered_production_trace_store(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    gate_payload = _production_trace_source_payload(
        repo,
        identity="tampered-store",
    )
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=gate_payload,
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    receipt, _ = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )
    with Path(receipt.trace_store_path).open("ab") as handle:
        handle.write(b"tampered")

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    assert result.status == "incomplete"
    assert manifest["production_trace"]["status"] == "incomplete"
    assert any(
        "sha256 differs" in issue
        for issue in manifest["production_trace"]["issues"]
    )


def test_release_export_rejects_hash_pinned_non_database_trace_authority(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="non-database",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    receipt_payload = receipt.to_dict()
    trace_store = Path(receipt.trace_store_path)
    gradebook = Path(receipt.gradebook_path)
    trace_store.write_bytes(b"attacker-controlled trace bytes")
    gradebook.write_bytes(b"attacker-controlled grade bytes")
    receipt_payload["trace_store_sha256"] = sha256(
        trace_store.read_bytes()
    ).hexdigest()
    receipt_payload["gradebook_sha256"] = sha256(
        gradebook.read_bytes()
    ).hexdigest()
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt_payload,
        },
        ts=1_784_000_001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    assert result.status == "incomplete"
    assert manifest["production_trace"]["status"] == "incomplete"
    assert any(
        "authority verification failed" in issue
        for issue in manifest["production_trace"]["issues"]
    )


def test_clean_room_verifier_rejects_recommitted_fake_trace_authority(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="clean-room-fake",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    receipt_payload = receipt.to_dict()
    trace_store = Path(receipt.trace_store_path)
    gradebook = Path(receipt.gradebook_path)
    trace_store.write_bytes(b"not a trace database")
    gradebook.write_bytes(b"not a grade database")
    receipt_payload["trace_store_sha256"] = sha256(
        trace_store.read_bytes()
    ).hexdigest()
    receipt_payload["gradebook_sha256"] = sha256(
        gradebook.read_bytes()
    ).hexdigest()
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt_payload,
        },
        ts=1_784_000_001,
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    trace_export = manifest["production_trace"]
    [record] = trace_export["records"]
    assert record["status"] == "incomplete"
    record["status"] = "complete"
    record["binding_status"] = "verified"
    record["authority_status"] = "verified"
    record["issues"] = []
    trace_export["status"] = "complete"
    trace_export["issues"] = []
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "authority verification failed" in issue
        for issue in verification.issues
    )


def test_clean_room_verifier_rejects_forged_trace_claim_ceiling(
    tmp_path: Path,
) -> None:
    _state_value, result, _source_ids, _recorded_ids, _receipts = (
        _export_valid_production_trace_package(tmp_path)
    )
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    [record] = manifest["production_trace"]["records"]
    receipt_path = result.output_dir / record["receipt_path"]
    receipt = strict_json_object_loads(
        receipt_path.read_text(encoding="utf-8")
    )
    receipt["claim_cap"] = "L6"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record["receipt_sha256"] = sha256(
        receipt_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "receipt claim_cap differs from persisted authority" in issue
        for issue in verification.issues
    )


def test_clean_room_verifier_requires_one_record_per_trace_ledger_event(
    tmp_path: Path,
) -> None:
    _state_value, result, _source_ids, recorded_ids, _receipts = (
        _export_valid_production_trace_package(
            tmp_path,
            gates=("execution", "outcome_review"),
        )
    )
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    records = manifest["production_trace"]["records"]
    assert [record["event_id"] for record in records] == list(recorded_ids)
    records.pop()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "production trace record coverage differs from the ledger" in issue
        for issue in verification.issues
    )


def test_release_export_requires_one_trace_per_accepted_runtime_gate(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    execution_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            gate="execution",
            identity="partial-coverage",
        ),
        ts=1_784_000_000,
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=execution_event_id,
        gate="execution",
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": execution_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_001,
    )
    uncovered_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Outcome accepted without trace authority.",
            decisions=["accept"],
        ),
        ts=1_784_000_002,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )
    trace_export = strict_json_object_loads(
        (result.output_dir / "replay" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )["production_trace"]

    assert result.status == "incomplete"
    assert trace_export["status"] == "incomplete"
    assert any(
        f"source event {uncovered_event_id}" in issue
        for issue in trace_export["issues"]
    )


def test_clean_room_verifier_rejects_explicitly_missing_required_trace(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="execution",
            summary="Execution accepted without trace authority.",
            decisions=["accept"],
        ),
        ts=1_784_000_000,
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "export",
    )
    manifest = strict_json_object_loads(
        (result.output_dir / "replay" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["production_trace"]["status"] == "incomplete"
    manifest["production_trace"] = {
        "schema_version": "dual-agent-production-trace-export/v1",
        "status": "missing",
        "records": [],
        "failed_attempts": [],
        "issues": ["no production trace event was exported"],
    }
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "required production trace authority is missing" in issue
        for issue in verification.issues
    )


def test_clean_room_verifier_rejects_forged_recovery_of_trace_failure(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted before trace persistence failed.",
            decisions=["accept"],
        ),
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    failed_event_id = _insert_event(
        state,
        kind="dual_agent_production_trace_failed",
        payload={
            "task_id": "task-1",
            "gate": "prd_review",
            "status": "blocked",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "reason": "production_trace_recording_failed",
            "error": "durable storage unavailable",
        },
        ts=1_784_000_001,
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "export",
    )
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    trace_export = manifest["production_trace"]
    assert trace_export["status"] == "incomplete"
    assert trace_export["failed_attempts"][0]["event_id"] == failed_event_id
    trace_export["status"] = "complete"
    trace_export["issues"] = []
    trace_export["failed_attempts"][0]["status"] = "recovered"
    trace_export["failed_attempts"][0]["recovered_by_event_id"] = 999_999
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "production trace failed-attempt recovery differs from the ledger"
        in issue
        for issue in verification.issues
    )


def test_release_export_recovers_transient_trace_failure_after_exact_success(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="recovered",
        ),
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    failed_event_id = _insert_event(
        state,
        kind="dual_agent_production_trace_failed",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "blocked",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "reason": "production_trace_recording_failed",
            "error": "temporary filesystem contention",
        },
        ts=1_784_000_001,
    )
    receipt, _ = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    recorded_event_id = _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_002,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    trace_export = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )["production_trace"]
    assert result.status == "ok"
    assert trace_export["status"] == "complete"
    assert trace_export["issues"] == []
    assert trace_export["failed_attempts"] == [{
        "event_id": failed_event_id,
        "source_event_id": source_event_id,
        "source_event_hash": source_event_hash,
        "status": "recovered",
        "recovered_by_event_id": recorded_event_id,
    }]


def test_release_export_keeps_conflicting_trace_failure_blocking(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            repo,
            identity="conflicting",
        ),
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    failed_event_id = _insert_event(
        state,
        kind="dual_agent_production_trace_failed",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "reason": "production_trace_recording_failed",
            "error": "identity changed",
        },
        ts=1_784_000_001,
    )
    receipt, _ = _record_canonical_production_trace(
        state,
        repo=repo,
        source_event_id=source_event_id,
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1_784_000_002,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    trace_export = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )["production_trace"]
    assert result.status == "incomplete"
    assert trace_export["status"] == "incomplete"
    assert trace_export["failed_attempts"] == [{
        "event_id": failed_event_id,
        "source_event_id": source_event_id,
        "source_event_hash": source_event_hash,
        "status": "blocking",
        "recovered_by_event_id": None,
    }]
    assert any(
        f"event {failed_event_id}" in issue
        for issue in trace_export["issues"]
    )


def test_release_export_rejects_production_trace_source_substitution(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="execution",
            summary="Execution accepted before source substitution.",
            decisions=["accept"],
        ),
        ts=1_784_000_000,
    )
    source_event = state.get_event(
        run_id="run-1",
        event_id=source_event_id,
    )
    assert source_event is not None
    source_event_hash = str(source_event["event_hash"])
    trace_root = (
        repo
        / ".codex-supervisor"
        / "production-traces"
        / source_event_hash
    )
    trace_root.mkdir(parents=True)
    trace_store = trace_root / "trace.db"
    gradebook = trace_root / "grades.db"
    trace_store.write_bytes(b"trace-store")
    gradebook.write_bytes(b"gradebook")
    substituted_hash = "f" * 64
    assert substituted_hash != source_event_hash
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": substituted_hash,
            "receipt": {
                "trace_store_path": str(trace_store),
                "trace_store_sha256": sha256(
                    trace_store.read_bytes()
                ).hexdigest(),
                "gradebook_path": str(gradebook),
                "gradebook_sha256": sha256(
                    gradebook.read_bytes()
                ).hexdigest(),
                "source_event_id": str(source_event_id),
                "source_event_hash": source_event_hash,
                "evidence": {
                    "run_id": "run-1",
                    "task_id": "task-1",
                    "gate": "execution",
                    "source_event_id": str(source_event_id),
                    "source_event_hash": source_event_hash,
                    "result_provenance": {
                        "result_receipt_hash": source_event_hash,
                    },
                },
            },
        },
        ts=1_784_000_001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=repo / "docs" / "dual-agent" / "task-1",
        require_complete_trace=True,
        trusted_workspace_root=repo,
    )

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    [record] = manifest["production_trace"]["records"]
    assert result.status == "incomplete"
    assert record["binding_status"] == "invalid"
    assert record["source_event"]["event_hash"] == source_event_hash
    assert any(
        "source_event_hash differs from the canonical ledger row" in issue
        for issue in record["issues"]
    )


def test_clean_room_export_rejects_forged_ledger_and_package_root(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted before public-package tampering.",
            decisions=["accept"],
        ),
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "export",
    )
    assert result.export_root_sha256 is not None

    integrity_path = result.output_dir / "replay" / "export-integrity.json"
    integrity = strict_json_object_loads(
        integrity_path.read_text(encoding="utf-8")
    )
    ledger_path = result.output_dir / integrity["ledger"]["path"]
    rows = [
        strict_json_object_loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["payload"]["task_id"] = "attacker-substituted-task"
    ledger_path.write_bytes(
        b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    )

    verification = verify_event_chain(
        rows,
        expected_run_id="run-1",
        expected_head_hash=integrity["ledger"]["head_event_hash"],
        expected_event_identity_hash=integrity["ledger"][
            "head_event_identity_hash"
        ],
    )
    assert not verification.valid
    assert verification.failure_code == "canonical_payload_hash_mismatch"

    ledger_descriptor = next(
        descriptor
        for descriptor in integrity["files"]
        if descriptor["path"] == integrity["ledger"]["path"]
    )
    assert sha256(ledger_path.read_bytes()).hexdigest() != ledger_descriptor[
        "sha256"
    ]

    ledger_descriptor["size"] = len(ledger_path.read_bytes())
    ledger_descriptor["sha256"] = sha256(
        ledger_path.read_bytes()
    ).hexdigest()
    integrity["ledger"]["sha256"] = ledger_descriptor["sha256"]
    integrity["file_tree_sha256"] = sha256(canonical_json_bytes({
        "schema_version": "dual-agent-public-export-file-tree/v1",
        "files": integrity["files"],
    })).hexdigest()
    forged_preimage = dict(integrity)
    forged_preimage.pop("export_root_sha256")
    integrity["export_root_sha256"] = sha256(
        canonical_json_bytes(forged_preimage)
    ).hexdigest()
    integrity_path.write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert integrity["export_root_sha256"] != result.export_root_sha256
    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=result.export_root_sha256,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert not verification.valid
    assert any(
        "expected export root" in issue
        for issue in verification.issues
    )


def test_export_projects_transcript_and_manifest_from_one_ledger_cut(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    first_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted before the ledger cut.",
            decisions=["accept"],
        ),
        ts=1_784_000_000,
    )

    class InsertImmediatelyBeforeCut:
        def __init__(self, delegate: State) -> None:
            self.delegate = delegate
            self.inserted_event_id: int | None = None

        def __getattr__(self, name: str):
            return getattr(self.delegate, name)

        def latest_event_id(self, run_id: str) -> int:
            if self.inserted_event_id is None:
                self.inserted_event_id = _insert_event(
                    self.delegate,
                    kind="dual_agent_gate_result",
                    payload=_result_payload(
                        gate="tdd_review",
                        summary="TDD accepted at the ledger cut.",
                        decisions=["accept"],
                    ),
                    ts=1_784_000_001,
                )
            return self.delegate.latest_event_id(run_id)

    cut_state = InsertImmediatelyBeforeCut(state)
    result = export_dual_agent_run_artifacts(
        cut_state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "export",
    )
    assert cut_state.inserted_event_id is not None

    manifest = json.loads(
        (result.output_dir / "replay" / "manifest.json").read_text()
    )
    transcript = [
        strict_json_object_loads(line)
        for line in (
            result.output_dir / "transcript.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    ledger = [
        strict_json_object_loads(line)
        for line in (
            result.output_dir / "replay" / "evidence-ledger.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    expected_event_ids = [
        first_event_id,
        cut_state.inserted_event_id,
    ]
    assert manifest["event_ids"] == expected_event_ids
    assert [event["event_id"] for event in transcript] == (
        expected_event_ids
    )
    assert [
        row["event_id"]
        for row in ledger
        if row["payload"].get("task_id") == "task-1"
    ] == expected_event_ids
    assert manifest["ledger"]["captured_head_event_id"] == (
        cut_state.inserted_event_id
    )


def test_reexport_replaces_destination_without_stale_file_leakage(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted before a clean re-export.",
            decisions=["accept"],
        ),
    )
    output_dir = tmp_path / "export"
    export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )
    stale_file = output_dir / "replay" / "stale-secret.txt"
    stale_file.write_text("must not survive\n", encoding="utf-8")

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )

    assert result.status == "ok"
    assert not stale_file.exists()
    integrity = strict_json_object_loads(
        (
            result.output_dir / "replay" / "export-integrity.json"
        ).read_text(encoding="utf-8")
    )
    assert "replay/stale-secret.txt" not in {
        descriptor["path"]
        for descriptor in integrity["files"]
    }


def test_export_rejects_preexisting_symlink_without_external_write(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted before symlink rejection.",
            decisions=["accept"],
        ),
    )
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("sentinel\n", encoding="utf-8")
    (output_dir / "index.md").symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        export_dual_agent_run_artifacts(
            state,
            run_id="run-1",
            task_id="task-1",
            output_dir=output_dir,
        )

    assert external.read_text(encoding="utf-8") == "sentinel\n"


def test_repeated_export_excludes_its_own_output_from_workspace_snapshot(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text(json.dumps({"cwd": str(repo), "planning_artifacts": []}))
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )
    output_dir = repo / "docs" / "dual-agent" / "task-1"

    first = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )
    first_snapshot = json.loads(
        (first.output_dir / "replay" / "workspace-snapshot.json").read_text()
    )
    second = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )
    second_snapshot = json.loads(
        (second.output_dir / "replay" / "workspace-snapshot.json").read_text()
    )

    assert second_snapshot["file_tree_sha256"] == first_snapshot["file_tree_sha256"]
    assert all(
        not entry["path"].startswith("docs/dual-agent/task-1/")
        for entry in second_snapshot["immutable_snapshot"]["entries"]
    )


def test_acceptance_snapshot_excludes_preexisting_task_artifacts(tmp_path):
    task_id = "trusted-task"
    safe_task_id = task_id
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    tracked_fixture = repo / "tests" / "fixtures" / "state.db"
    tracked_fixture.parent.mkdir(parents=True)
    tracked_fixture.write_bytes(b"immutable tracked fixture")
    subprocess.run(
        ["git", "add", "README.md", "tests/fixtures/state.db"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_fixture.write_bytes(b"modified tracked fixture")

    output_dir = repo / "docs" / "dual-agent" / safe_task_id
    source = output_dir / "source" / "prd.md"
    source.parent.mkdir(parents=True)
    source.write_text("# PRD\n\nAccepted input.\n", encoding="utf-8")
    stale_replay = output_dir / "replay" / "workspace-snapshot.json"
    stale_replay.parent.mkdir(parents=True)
    stale_replay.write_text("recursive-marker\n" * 1000, encoding="utf-8")
    custom_replay = repo / "custom-export" / "replay"
    custom_replay.mkdir(parents=True)
    (custom_replay / "workspace-snapshot.json").write_text(
        "custom-recursive-marker\n" * 1000,
        encoding="utf-8",
    )
    (custom_replay / "source.py").write_text(
        "VALUE = 'must remain visible'\n",
        encoding="utf-8",
    )
    (custom_replay / "manifest.json").write_text(
        '{"workspace_snapshot":"embedded"}\n',
        encoding="utf-8",
    )
    unrelated_source = repo / "docs" / "unrelated" / "source.py"
    unrelated_source.parent.mkdir(parents=True)
    unrelated_source.write_text("VALUE = 1\n", encoding="utf-8")
    sibling_source = (
        repo
        / "docs"
        / "dual-agent"
        / "sibling-task"
        / "source.py"
    )
    sibling_source.parent.mkdir(parents=True)
    sibling_source.write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "state.db").write_bytes(b"runtime database")
    (repo / "state.db-wal").write_bytes(b"runtime wal")
    (repo / "state.db-shm").write_bytes(b"runtime shm")
    (repo / "autoresearch-state.db").write_bytes(b"runtime autoresearch database")
    (repo / "experiments.db").write_bytes(b"runtime experiment database")
    handoff = repo / ".handoff" / "task.json"
    handoff.parent.mkdir()
    handoff.write_text(
        json.dumps({
            "task_id": "sibling-task",
            "cwd": str(repo),
            "planning_artifacts": [{
                "kind": "prd",
                "path": f"docs/dual-agent/{safe_task_id}/source/prd.md",
                "sha256": sha256(source.read_bytes()).hexdigest(),
            }],
        }),
        encoding="utf-8",
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                task_id=task_id,
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id=task_id,
        output_dir=output_dir,
    )
    snapshot = json.loads(
        (result.output_dir / "replay" / "workspace-snapshot.json").read_text()
    )

    assert snapshot["source_artifact_hashes"]["prd"] == sha256(
        source.read_bytes()
    ).hexdigest()
    snapshot_paths = {
        entry["path"]
        for entry in snapshot["immutable_snapshot"]["entries"]
    }
    assert f"docs/dual-agent/{safe_task_id}/source/prd.md" in snapshot_paths
    assert f"docs/dual-agent/{safe_task_id}/replay/workspace-snapshot.json" not in snapshot_paths
    assert "custom-export/replay/source.py" in snapshot_paths
    assert "docs/unrelated/source.py" in snapshot_paths
    assert "docs/dual-agent/sibling-task/source.py" in snapshot_paths
    assert "tests/fixtures/state.db" in snapshot_paths
    assert snapshot_paths.isdisjoint({
        "state.db",
        "state.db-wal",
        "state.db-shm",
        "autoresearch-state.db",
        "experiments.db",
    })


def test_posthoc_snapshot_preserves_declared_planning_artifact_bytes(
    tmp_path,
):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    output_dir = repo / "docs" / "dual-agent" / "task-1"
    source = output_dir / "source" / "prd.md"
    source.parent.mkdir(parents=True)
    source.write_text("# PRD\n\nPosthoc input.\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text(
        json.dumps({
            "task_id": "task-1",
            "cwd": str(repo),
            "planning_artifacts": [{
                "kind": "prd",
                "path": "docs/dual-agent/task-1/source/prd.md",
                "sha256": sha256(source_bytes).hexdigest(),
            }],
        }),
        encoding="utf-8",
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Outcome blocked.",
                decisions=["revise"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )
    snapshot = json.loads(
        (
            result.output_dir
            / "replay"
            / "workspace-snapshot.json"
        ).read_text(encoding="utf-8")
    )

    assert snapshot["capture_source"] == "posthoc_diagnostic"
    [entry] = [
        item
        for item in snapshot["immutable_snapshot"]["entries"]
        if item["path"]
        == "docs/dual-agent/task-1/source/prd.md"
    ]
    assert base64.b64decode(entry["content_base64"]) == source_bytes
    assert not source.exists()


def test_workspace_snapshot_hash_ignores_runtime_cache_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)

    cache = repo / ".claude"
    cache.mkdir()
    (cache / "large-cache.bin").write_bytes(b"a" * 1024)
    before = _file_tree_sha256(repo)

    (cache / "large-cache.bin").write_bytes(b"b" * 1024)
    after = _file_tree_sha256(repo)

    assert after == before


def test_export_dual_agent_run_artifacts_writes_run_level_failure_summary(tmp_path):
    state = _state(tmp_path)
    taxonomy = {
        "category": "task_verification",
        "subcategory": "missing_or_stale_receipt",
        "code": "workflow_claim_verification_failed",
        "mast_code": "FM-3.2",
        "mast_mode": "No or incomplete verification",
        "mast_category": "Task Verification",
    }
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claims lacked receipts.",
                decisions=["revise"],
            ),
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "blocked",
                "failure_taxonomy": taxonomy,
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["failure_summary"] == {
        "event_id": event_id,
        "policy_verdict": "blocked",
        "failure_taxonomy": taxonomy,
    }
    interactions = (result.output_dir / "interactions.md").read_text()
    assert "FM-3.2" in interactions
    assert "No or incomplete verification" in interactions


def test_export_dual_agent_run_artifacts_writes_fast_triage_page_and_source_links(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claims lacked receipts.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "blocked",
            "claim_verification": {
                "probe_id": "P11",
                "status": "red",
                "reason": "workflow_claim_verification_failed",
                "details": {
                    "failures": [
                        "tests_passed_without_test_receipt",
                        "implemented_without_diff_receipt",
                    ],
                    "receipts": [],
                },
            },
            "tool_calls": [
                {
                    "name": "start_dual_agent_gate",
                    "status": "completed",
                    "duration_ms": 2200,
                    "started_at_ms": 1000,
                    "ended_at_ms": 3200,
                    "result_summary": {
                        "claude_gate_status": "accepted",
                        "supervisor_final_status": "blocked",
                    },
                },
                {
                    "name": "verify_workflow_claims",
                    "status": "red",
                    "duration_ms": 13,
                    "started_at_ms": 3201,
                    "ended_at_ms": 3214,
                    "receipt_ids": [],
                    "result_summary": {
                        "reason": "workflow_claim_verification_failed",
                        "failures": [
                            "tests_passed_without_test_receipt",
                            "implemented_without_diff_receipt",
                        ],
                    },
                },
            ],
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "code": "workflow_claim_verification_failed",
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                    "mast_category": "Task Verification",
                },
                "tool_calls": [
                    {
                        "name": "start_dual_agent_gate",
                        "status": "completed",
                        "duration_ms": 2200,
                        "started_at_ms": 1000,
                        "ended_at_ms": 3200,
                        "result_summary": {
                            "claude_gate_status": "accepted",
                            "supervisor_final_status": "blocked",
                        },
                    },
                    {
                        "name": "verify_workflow_claims",
                        "status": "red",
                        "duration_ms": 13,
                        "started_at_ms": 3201,
                        "ended_at_ms": 3214,
                        "receipt_ids": [],
                        "result_summary": {
                            "reason": "workflow_claim_verification_failed",
                            "failures": [
                                "tests_passed_without_test_receipt",
                                "implemented_without_diff_receipt",
                            ],
                        },
                    },
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "receipt_gate_decision",
            "content": "Blocked on missing receipts.",
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_interaction_message",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [
                    {
                        "name": "verify_workflow_claims",
                        "status": "red",
                        "duration_ms": 13,
                        "started_at_ms": 3201,
                        "ended_at_ms": 3214,
                        "tokens_in": 5,
                        "tokens_out": 7,
                        "cost_usd": 0.12,
                        "result_summary": {
                            "reason": "workflow_claim_verification_failed",
                            "failures": [
                                "tests_passed_without_test_receipt",
                                "implemented_without_diff_receipt",
                            ],
                        },
                    },
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    assert (result.output_dir / "triage.md") in result.files
    index = (result.output_dir / "index.md").read_text()
    triage = (result.output_dir / "triage.md").read_text()
    assert "[Triage](triage.md)" in index
    assert "[Source PRD](source/prd.md)" in index
    assert "[Source TDD](source/tdd.md)" in index
    assert "workflow_claim_verification_failed" in triage
    assert "FM-3.2" in triage
    assert "claude_gate_status: `accepted`" in triage
    assert "supervisor_final_status: `blocked`" in triage
    assert "## Run Totals" in triage
    assert "- unique_tool_calls: `2`" in triage
    assert "tests_passed_without_test_receipt" in triage
    assert "implemented_without_diff_receipt" in triage
    assert "missing:tests_passed_without_test_receipt" in triage
    assert "missing:implemented_without_diff_receipt" in triage
    assert "workflow_claim_verification_failed" in triage
    assert "| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |" in triage
    assert "verify_workflow_claims" in triage
    assert "claude_gate_status" in triage
    assert "supervisor_final_status" in triage
    assert "Next Safe Action" in triage
    assert (result.output_dir / "replay" / "workspace-snapshot.json") in result.files
    assert (result.output_dir / "mast-coverage.md") in result.files
    coverage = (result.output_dir / "mast-coverage.md").read_text()
    assert "| FM-3.2 | Task Verification | No or incomplete verification | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-1.3 | Specification Issues | Step repetition | not_observed_in_run | covered_by_deterministic_probe |" in coverage
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["tool_call_totals"]["unique_tool_calls"] == 2
    assert manifest["tool_call_totals"]["total_tokens_in"] == 5
    assert manifest["tool_call_totals"]["total_tokens_out"] == 7
    assert manifest["tool_call_totals"]["total_cost_usd"] == 0.12
    assert manifest["mast_coverage"][0]["mast_code"] == "FM-1.1"
    assert manifest["mast_coverage"][0]["deterministic_status"] == "covered_by_deterministic_probe"
    mast_coverage_json = json.loads((result.output_dir / "replay" / "mast-coverage.json").read_text())
    assert mast_coverage_json == manifest["mast_coverage"]


def test_export_dual_agent_run_artifacts_writes_final_event_id_for_accepted_triage(tmp_path):
    state = _state(tmp_path)
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="accepted",
                summary="Both reviewers accepted.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "accepted",
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    triage = (result.output_dir / "triage.md").read_text()
    assert f"- final_event_id: `{event_id}`" in triage
    assert "- supervisor_final_status: `accepted`" in triage


def test_export_dual_agent_run_artifacts_ignores_recovered_block_in_triage(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claim verification failed.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "blocked",
            "claim_verification": {
                "status": "red",
                "reason": "workflow_claim_verification_failed",
                "details": {
                    "failures": ["implemented_without_diff_receipt"],
                },
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "code": "claim_verification_failed",
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                },
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
        ts=1000,
    )
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="accepted",
                summary="Claim verification recovered.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "accepted",
            "claim_verification": {
                "status": "green",
                "reason": "workflow_claims_verified",
                "details": {},
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "accepted",
                "failure_taxonomy": None,
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
        ts=1001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    triage = (result.output_dir / "triage.md").read_text()
    assert f"- final_event_id: `{event_id}`" in triage
    assert "- policy_verdict: `observed`" in triage
    assert "- supervisor_final_status: `accepted`" in triage
    assert "No blocking failure taxonomy recorded." in triage
    assert "implemented_without_diff_receipt" not in triage


def test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics(tmp_path):
    state = _state(tmp_path)
    first = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="Accepted too soon.", decisions=["accept"]),
            "required_probes": ["P1", "P2", "P3", "CURSOR"],
            "probes": {
                "P1": {"probe_id": "P1", "status": "green", "reason": "ok", "details": {}},
                "P2": {"probe_id": "P2", "status": "green", "reason": "ok", "details": {}},
                "P3": {"probe_id": "P3", "status": "green", "reason": "ok", "details": {}},
            },
            "handoff_packet_sha256": "same-packet",
        },
    )
    duplicate = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="Accepted too soon.", decisions=["accept"]),
            "handoff_packet_sha256": "same-packet",
        },
        ts=1001,
    )
    round_event = _insert_event(
        state,
        kind="dual_agent_gate_round",
        payload=_round_payload(
            gate="outcome_review",
            round_index=2,
            codex_decision="deny",
            claude_decision="accept",
            objection="no tests added",
        ),
        ts=1002,
    )
    claude_response = _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "schema_version": "dual-agent-interaction/v1",
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "claude_code",
            "recipient": "codex",
            "message_type": "gate_response",
            "content": "Ready to proceed.",
            "addresses": [],
            "claims": [],
            "objections": [],
            "metadata": {},
        },
        ts=1003,
    )
    cursor_reject = _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "accepted": False,
            "probe": {"probe_id": "CURSOR", "status": "red", "reason": "cursor_review_failed"},
        },
        ts=1004,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    failures = manifest["sequence_failures"]
    by_code = {failure["mast_code"]: failure for failure in failures}
    assert by_code["FM-3.1"]["event_ids"] == [first]
    assert by_code["FM-1.3"]["event_ids"] == [first, duplicate]
    assert by_code["FM-2.5"]["event_ids"] == [round_event, claude_response]
    assert by_code["FM-3.3"]["event_ids"] == [duplicate, cursor_reject]
    coverage = (result.output_dir / "mast-coverage.md").read_text()
    assert "| FM-1.3 | Specification Issues | Step repetition | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-2.5 | Inter-Agent Misalignment | Ignored other agent input | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-3.1 | Task Verification | Premature termination | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-3.3 | Task Verification | Incorrect verification | observed_in_run | covered_by_deterministic_probe |" in coverage


def test_export_dual_agent_run_artifacts_copies_screenshots_and_writes_manifest(tmp_path):
    state = _state(tmp_path)
    source_event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_production_trace_source_payload(
            tmp_path,
            gate="outcome_review",
            identity="screenshots",
        ),
    )
    receipt, source_event_hash = _record_canonical_production_trace(
        state,
        repo=tmp_path,
        source_event_id=source_event_id,
        gate="outcome_review",
    )
    _insert_event(
        state,
        kind="dual_agent_production_trace_recorded",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "recorded",
            "source_event_id": source_event_id,
            "source_event_hash": source_event_hash,
            "receipt": receipt.to_dict(),
        },
        ts=1001,
    )
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
        screenshots=(
            ScreenshotArtifact(
                path=screenshot,
                label="Desktop final state",
                note="Captured by Codex after implementation.",
                source="computer_use",
                validation_status="passed",
                validation_notes="Visual state matches the acceptance criteria.",
            ),
        ),
        trusted_workspace_root=tmp_path,
    )

    copied = result.output_dir / "screenshots" / "01-desktop-final-state.png"
    manifest = result.output_dir / "screenshots.md"

    assert copied.read_bytes() == screenshot.read_bytes()
    assert "![Desktop final state](screenshots/01-desktop-final-state.png)" in manifest.read_text()
    assert "Captured by Codex after implementation." in manifest.read_text()
    assert "- source: `computer_use`" in manifest.read_text()
    assert "- validation_status: `passed`" in manifest.read_text()
    assert "Visual state matches the acceptance criteria." in manifest.read_text()
    assert copied in result.files
    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=result.export_root_sha256,
        expected_ledger_head=result.ledger_head_hash,
    )
    assert verification.valid, verification.issues


def test_export_rejects_symlinked_screenshot_source(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Visual review accepted.",
            decisions=["accept"],
        ),
    )
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do not copy\n", encoding="utf-8")
    capture = tmp_path / "capture.png"
    capture.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        export_dual_agent_run_artifacts(
            state,
            run_id="run-1",
            task_id="task-1",
            output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
            screenshots=(
                ScreenshotArtifact(path=capture, label="Unsafe capture"),
            ),
            trusted_workspace_root=tmp_path,
        )

    assert outside.read_text(encoding="utf-8") == "do not copy\n"


def test_release_export_requires_authoritative_ledger_checkpoint(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Structurally valid but unanchored.",
            decisions=["accept"],
        ),
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "diagnostic-export",
        require_authoritative_ledger=True,
    )

    assert result.status == "incomplete"
    assert result.ledger_authoritative is False
    integrity = strict_json_object_loads(
        (
            result.output_dir / "replay" / "export-integrity.json"
        ).read_text(encoding="utf-8")
    )
    assert integrity["ledger"]["authoritative_head_verified"] is False
    assert integrity["ledger"]["authority_failure_code"] == (
        "trusted_head_required"
    )


def test_release_export_is_ok_with_authoritative_ledger_checkpoint(tmp_path):
    state = _authoritative_state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Anchored export.",
            decisions=["accept"],
        ),
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "authoritative-export",
        require_authoritative_ledger=True,
    )

    assert result.status == "ok"
    assert result.ledger_authoritative is True


def test_clean_room_verifier_rejects_task_event_manifest_substitution(tmp_path):
    state = _state(tmp_path)
    task_one_event = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            task_id="task-1",
            gate="outcome_review",
            summary="Task one accepted.",
            decisions=["accept"],
        ),
    )
    task_two_event = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            task_id="task-2",
            gate="outcome_review",
            summary="Task two accepted.",
            decisions=["accept"],
        ),
        ts=1001,
    )
    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "export",
    )
    manifest_path = result.output_dir / "replay" / "manifest.json"
    manifest = strict_json_object_loads(
        manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["event_ids"] == [task_one_event]
    manifest["task_id"] = "task-2"
    manifest["event_ids"] = [task_two_event]
    manifest["events_count"] = 1
    manifest["state"]["first_event_id"] = task_two_event
    manifest["state"]["last_event_id"] = task_two_event
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_root = _recommit_export_integrity(result.output_dir)

    verification = verify_dual_agent_export(
        result.output_dir,
        expected_root=forged_root,
        expected_ledger_head=result.ledger_head_hash,
    )

    assert not verification.valid
    assert any(
        "task event projection" in issue
        or "transcript event projection" in issue
        for issue in verification.issues
    )


def test_export_dual_agent_run_artifacts_labels_handoff_workspace_head(tmp_path):
    repo = tmp_path / "sandbox"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("# sandbox\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    handoff_dir = repo / ".handoff"
    handoff_dir.mkdir()
    handoff = handoff_dir / "task-1.json"
    handoff.write_text(json.dumps({"cwd": str(repo), "planning_artifacts": []}))
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="ok", decisions=["accept"]),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    snapshot = json.loads((result.output_dir / "replay" / "workspace-snapshot.json").read_text())
    assert snapshot["root_source"] == "handoff_cwd"
    assert snapshot["git"]["head_label"] == "handoff_cwd_head"


def test_export_dual_agent_run_artifacts_includes_artifact_rigor_details(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "attempts": 0,
            "handoff_packet_path": None,
            "probes": {},
            "outcome": None,
            "escalation": {
                "type": "artifact_rigor",
                "reason": "required_artifacts_missing",
            },
            "artifact_rigor": {
                "status": "blocked",
                "reason": "required_artifacts_missing",
                "required_artifacts": ["prd", "tdd_plan", "grill_findings", "issues"],
                "present_artifacts": ["prd"],
                "missing_artifacts": ["tdd_plan", "grill_findings", "issues"],
                "required_prerequisite_gates": ["prd_review", "issues_review", "tdd_review"],
                "accepted_prerequisite_gates": ["prd_review"],
                "missing_prerequisite_gates": ["issues_review", "tdd_review"],
                "user_facing": True,
                "screenshots": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    outcome_review = (result.output_dir / "outcome-review.md").read_text()

    assert "### Artifact Rigor" in outcome_review
    assert "- status: `blocked`" in outcome_review
    assert "- reason: `required_artifacts_missing`" in outcome_review
    assert "- required_artifacts: `prd`, `tdd_plan`, `grill_findings`, `issues`" in outcome_review
    assert "- missing_artifacts: `tdd_plan`, `grill_findings`, `issues`" in outcome_review
    assert "- required_prerequisite_gates: `prd_review`, `issues_review`, `tdd_review`" in outcome_review
    assert "- accepted_prerequisite_gates: `prd_review`" in outcome_review
    assert "- missing_prerequisite_gates: `issues_review`, `tdd_review`" in outcome_review
    assert "- user_facing: `True`" in outcome_review


def test_export_dual_agent_run_artifacts_reports_not_found_without_writing(tmp_path):
    state = _state(tmp_path)
    output_dir = tmp_path / "missing"

    result = export_dual_agent_run_artifacts(
        state,
        run_id="missing",
        task_id="missing",
        output_dir=output_dir,
    )

    assert result.status == "not_found"
    assert result.files == ()
    assert not output_dir.exists()


def test_maybe_artifact_converts_mcp_payload_to_planning_artifact(tmp_path):
    artifact_path = tmp_path / "docs" / "dual-agent" / "task" / "prd.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# PRD\n")

    artifact = _maybe_artifact({"path": str(artifact_path), "kind": "prd", "mutable_by_worker": False})

    assert artifact is not None
    assert artifact.path == artifact_path
    assert artifact.kind == "prd"
    assert artifact.mutable_by_worker is False


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_exports_artifacts_and_accepts_planning_artifacts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.config import Config
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    class FakeMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools = {}

        def tool(self):
            def decorate(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorate

    async def maybe(value):
        import inspect

        if inspect.isawaitable(value):
            return await value
        return value

    cfg = Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path / "sessions"), "cli_command": "codex"}},
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })
    state = State(str(tmp_path / "state.db"))
    artifact_dir = tmp_path / "docs" / "dual-agent" / "gate-1"
    artifact_dir.mkdir(parents=True)
    prd = artifact_dir / "prd.md"
    prd.write_text((FIXTURE_ROOT / "prd" / "good.md").read_text(encoding="utf-8"), encoding="utf-8")
    screenshot = tmp_path / "desktop.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )

    def fake_runner(argv, **kwargs):
        transcript = (
            "<dual_agent_outcome>"
            + json.dumps({
                "task_id": "gate-1",
                "summary": "Reviewed with artifacts.",
                "specialists": [{"name": "Planner", "decision": "accept"}],
                "decisions": ["accept"],
                "objections": [],
                "changed_files": [],
                "tests": [],
                "test_status": "passed",
                "confidence": 0.9,
                "claims": [],
            })
            + "</dual_agent_outcome>"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=build_lead_replay_stdout(transcript), stderr="")

    server = build_codex_supervisor_mcp_server(cfg, state, mcp_cls=FakeMCP, runner=fake_runner)

    assert "export_gate_artifacts" in server.tools
    result = await maybe(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept"],
        expected_objections=[],
        planning_artifacts=[{"path": str(prd), "kind": "prd", "mutable_by_worker": False}],
    ))
    exported = await maybe(server.tools["export_gate_artifacts"](
        run_id="run-1",
        task_id="gate-1",
        cwd=str(tmp_path),
        screenshots=[{
            "path": str(screenshot),
            "label": "Desktop",
            "note": "Generated by Codex Browser.",
            "source": "browser",
            "validation_status": "passed",
            "validation_notes": "Browser screenshot reviewed against visual acceptance criteria.",
        }],
    ))

    assert result["status"] == "accepted"
    assert result["probes"]["P1"]["status"] == "green"
    assert exported["status"] == "incomplete"
    assert (
        "docs/dual-agent/gate-1/release/source/prd.md"
        in exported["files"]
    )
    assert (
        "docs/dual-agent/gate-1/release/screenshots.md"
        in exported["files"]
    )
    assert (
        "docs/dual-agent/gate-1/release/screenshots/01-desktop.png"
        in exported["files"]
    )
