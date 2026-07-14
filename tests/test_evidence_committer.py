from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from supervisor.claim_gate import ClaimGate
from supervisor.evidence_committer import (
    EVIDENCE_COMMIT_EVENT_KIND,
    EvidenceArtifact,
    EvidenceCommitConflict,
    EvidenceCommitIntegrityError,
    EvidenceCommitRequest,
    EvidenceCommitter,
    EvidenceGradeHistory,
    HmacCheckpointAuthority,
    _write_evidence_file,
)
from supervisor.evidence_ledger import canonical_json_bytes
from supervisor.experiment_kernel import (
    Arm,
    GradeRevisionRef,
    SqliteExperimentStore,
)
from supervisor.grade_revisions import (
    GradeBook,
    GradeTerminalCommit,
    RunEnvelopeRef,
)
from supervisor.ledger_checkpoints import checkpoint_identity
from supervisor.state import State
from supervisor.target.types import ScopeContract
from supervisor.task_environment import FrozenTaskResult, Grade
from supervisor.trace_graph import (
    EdgeType,
    NodeType,
    TraceClosureBinding,
    TraceEdge,
    TraceGraph,
    TraceIdentity,
    TraceNode,
    TracePlanningArtifactRef,
    canonical_revision_hash,
    trace_instance_id_from_hash,
)


class SimulatedCrash(RuntimeError):
    pass


class _IndependentCheckpointPins:
    """Test double for a rollback-independent trusted pin domain."""

    def __init__(self) -> None:
        self._history: dict[bytes, dict[str, Any]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def pin(self, identity: Mapping[str, Any]) -> None:
        value = dict(identity)
        encoded = canonical_json_bytes(value)
        run_id = str(value["run_id"])
        with self._lock:
            current = self._latest.get(run_id)
            if current is not None:
                current_count = int(current["event_count"])
                new_count = int(value["event_count"])
                if new_count < current_count:
                    raise RuntimeError("trusted checkpoint pin rollback")
                if (
                    new_count == current_count
                    and canonical_json_bytes(current) != encoded
                ):
                    raise RuntimeError("trusted checkpoint pin fork")
            self._history[encoded] = value
            self._latest[run_id] = value

    def get(
        self,
        identity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        with self._lock:
            value = self._history.get(canonical_json_bytes(dict(identity)))
            return None if value is None else dict(value)

    def latest(self, run_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            value = self._latest.get(str(run_id))
            return None if value is None else dict(value)


def test_evidence_request_fingerprint_binds_expected_workflow_context(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = TracePlanningArtifactRef(
        kind="implementation_plan",
        path="/repo/implementation-plan.md",
        sha256="9" * 64,
    )
    first_binding = TraceClosureBinding(
        task_id="task-1",
        run_id="workflow-1",
        gate="execution",
        planning_artifacts=(artifact,),
    )
    second_binding = TraceClosureBinding(
        task_id="task-1",
        run_id="workflow-2",
        gate="execution",
        planning_artifacts=(artifact,),
    )
    first = replace(
        fixture.request,
        trace_graph=fixture.request.trace_graph.bind_validation(
            expected_binding=first_binding
        ),
    )
    second = replace(
        fixture.request,
        trace_graph=fixture.request.trace_graph.bind_validation(
            expected_binding=second_binding
        ),
    )

    assert canonical_json_bytes(first.fingerprint_payload()) != (
        canonical_json_bytes(second.fingerprint_payload())
    )


class _CountingCheckpointAuthority:
    def __init__(self) -> None:
        self._delegate = HmacCheckpointAuthority(
            key_id="counting-hermetic-key",
            key=b"counting-hermetic-checkpoint-key",
        )
        self.key_id = self._delegate.key_id
        self.algorithm = self._delegate.algorithm
        self.sign_calls = 0

    def sign(self, payload: bytes) -> bytes:
        self.sign_calls += 1
        return self._delegate.sign(payload)

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        return self._delegate.verify(payload, signature)


class _RandomizedCheckpointAuthority:
    key_id = "randomized-hermetic-key"
    algorithm = "hmac-sha256-with-random-nonce"

    def __init__(self, *, fail_on_sign_call: int | None = None) -> None:
        self._key = b"randomized-hermetic-checkpoint-key"
        self.fail_on_sign_call = fail_on_sign_call
        self.sign_calls = 0

    def sign(self, payload: bytes) -> Mapping[str, str]:
        self.sign_calls += 1
        if self.sign_calls == self.fail_on_sign_call:
            raise SimulatedCrash("power loss during checkpoint signing")
        nonce = base64.b64encode(os.urandom(16)).decode("ascii")
        signature = hmac.new(
            self._key,
            payload + nonce.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return {
            "nonce": nonce,
            "signature": base64.b64encode(signature).decode("ascii"),
        }

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        if (
            signature.get("key_id") != self.key_id
            or signature.get("algorithm") != self.algorithm
        ):
            return False
        nonce = str(signature.get("nonce") or "")
        try:
            expected = base64.b64encode(
                hmac.new(
                    self._key,
                    payload + nonce.encode("ascii"),
                    hashlib.sha256,
                ).digest()
            ).decode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(
            str(signature.get("signature") or ""),
            expected,
        )


def test_evidence_commit_rejects_trace_that_drops_grade_lineage(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    retained_nodes = tuple(
        node
        for node in fixture.request.trace_graph.nodes
        if node.attributes.get("record_kind") not in {
            "grade_revision",
            "grade_invalidation",
        }
        or node.attributes.get("revision_number") == 2
    )
    retained_identities = {node.identity for node in retained_nodes}
    stripped_graph = TraceGraph(
        nodes=retained_nodes,
        edges=tuple(
            edge
            for edge in fixture.request.trace_graph.edges
            if edge.source in retained_identities
            and edge.target in retained_identities
        ),
    )
    request = replace(fixture.request, trace_graph=stripped_graph)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="complete GradeBook lineage",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            request
        )


def test_evidence_commit_recomputes_claim_cap_from_bound_evidence(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    report_artifact = next(
        artifact
        for artifact in fixture.request.artifacts
        if artifact.role == "claim_report"
    )
    forged_report = json.loads(report_artifact.content)
    forged_report["claim_gate"]["max_claim_level"] = "L2"
    forged_artifacts = tuple(
        replace(
            artifact,
            content=canonical_json_bytes(forged_report),
        )
        if artifact.role == "claim_report"
        else artifact
        for artifact in fixture.request.artifacts
    )

    with pytest.raises(
        EvidenceCommitConflict,
        match="not authorized by its bound evidence bundle",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            replace(
                fixture.request,
                claim_cap="L2",
                artifacts=forged_artifacts,
            )
        )


def test_evidence_commit_rejects_acknowledged_but_unresolved_stale_grade(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    history = fixture.request.grade_histories[0]
    stale_revision = history.revisions[0]
    invalidation = history.invalidations[0]
    decision = next(
        node
        for node in fixture.request.trace_graph.nodes
        if node.identity.node_type is NodeType.DEC
    )
    unresolved_decision = replace(
        decision,
        attributes={
            "grade_citations": [
                {
                    "grade_id": stale_revision.grade_id,
                    "revision_hash": stale_revision.revision_hash,
                    "acknowledged_invalidation_hashes": [
                        invalidation.invalidation_hash
                    ],
                    "resolution_grade_id": None,
                    "resolution_revision_hash": None,
                }
            ],
        },
    )
    graph = TraceGraph(
        nodes=tuple(
            unresolved_decision
            if node.identity == decision.identity
            else node
            for node in fixture.request.trace_graph.nodes
        ),
        edges=fixture.request.trace_graph.edges,
    )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="stale_grade_unresolved",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            replace(fixture.request, trace_graph=graph)
        )


def test_evidence_commit_accepts_stale_grade_with_exact_current_resolution(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    history = fixture.request.grade_histories[0]
    stale_revision, current_revision = history.revisions
    invalidation = history.invalidations[0]
    decision = next(
        node
        for node in fixture.request.trace_graph.nodes
        if node.identity.node_type is NodeType.DEC
    )
    resolved_decision = replace(
        decision,
        attributes={
            "grade_citations": [
                {
                    "grade_id": stale_revision.grade_id,
                    "revision_hash": stale_revision.revision_hash,
                    "acknowledged_invalidation_hashes": [
                        invalidation.invalidation_hash
                    ],
                    "resolution_grade_id": current_revision.grade_id,
                    "resolution_revision_hash": (
                        current_revision.revision_hash
                    ),
                }
            ],
        },
    )
    graph = TraceGraph(
        nodes=tuple(
            resolved_decision
            if node.identity == decision.identity
            else node
            for node in fixture.request.trace_graph.nodes
        ),
        edges=fixture.request.trace_graph.edges,
    )
    gradebook_path = fixture.committer_arguments["gradebook_path"]
    assert isinstance(gradebook_path, Path)
    with GradeBook(gradebook_path) as gradebook:
        closure = graph.validate_closure(
            now=fixture.request.closure_time,
            decision_grade_validator=gradebook,
        )
    trace_content = canonical_json_bytes({
        "graph": graph.to_dict(),
        "closure": closure.to_dict(),
    })
    artifacts = tuple(
        replace(artifact, content=trace_content)
        if artifact.role == "trace_graph"
        else artifact
        for artifact in fixture.request.artifacts
    )
    artifacts = _rebind_fixture_claim_authority(artifacts)

    result = EvidenceCommitter(
        **fixture.committer_arguments
    ).commit(
        replace(
            fixture.request,
            trace_graph=graph,
            artifacts=artifacts,
        )
    )

    assert result.status == "complete"


def test_evidence_commit_rejects_terminal_grade_authority_without_arm_event(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    experiment_path = fixture.committer_arguments["experiment_db_path"]
    assert isinstance(experiment_path, Path)
    with sqlite3.connect(experiment_path) as connection:
        connection.execute("DROP TRIGGER experiment_arm_states_no_delete")
        connection.execute(
            """
            DELETE FROM experiment_arm_state_events
            WHERE state IN (
              'completed', 'failed', 'common_infrastructure_failed'
            )
            """
        )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal grade authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_evidence_commit_rejects_history_that_omits_terminal_commits(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    history = fixture.request.grade_histories[0]
    request = replace(
        fixture.request,
        grade_histories=(
            replace(history, terminal_commits=()),
        ),
    )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal_commits",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(request)


def test_evidence_commit_requires_one_terminal_commit_per_revision(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    history = fixture.request.grade_histories[0]
    missing_commit = history.terminal_commits[0]
    gradebook_path = fixture.committer_arguments["gradebook_path"]
    assert isinstance(gradebook_path, Path)
    with sqlite3.connect(gradebook_path) as connection:
        connection.execute("DROP TRIGGER grade_terminal_commits_no_delete")
        connection.execute(
            "DELETE FROM grade_terminal_commits WHERE grade_id=?",
            (missing_commit.grade_id,),
        )
    request = replace(
        fixture.request,
        grade_histories=(
            replace(
                history,
                terminal_commits=history.terminal_commits[1:],
            ),
        ),
    )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="exactly one terminal commit per published revision",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(request)


@pytest.mark.parametrize("malformation", ("extra", "duplicate"))
def test_evidence_commit_rejects_extra_or_duplicate_terminal_grade_ids(
    tmp_path: Path,
    malformation: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    history = fixture.request.grade_histories[0]
    terminal_commits = history.terminal_commits
    if malformation == "extra":
        terminal_commits = (
            *terminal_commits,
            replace(
                terminal_commits[0],
                commit_id="terminal_commit_extra",
                commit_hash="e" * 64,
                grade_id="grade_extra",
            ),
        )
    else:
        terminal_commits = (*terminal_commits, terminal_commits[0])
    request = replace(
        fixture.request,
        grade_histories=(
            replace(history, terminal_commits=terminal_commits),
        ),
    )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="no missing, extra, or duplicate grade IDs",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(request)


def test_evidence_commit_rejects_mismatched_terminal_grade_reference(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path,
        mismatched_terminal_grade_reference=True,
    )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal arm event grade reference",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


@pytest.mark.parametrize("tamper", ("row_identity", "terminal_state_hash"))
def test_evidence_commit_rejects_mismatched_terminal_arm_authority(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    _tamper_experiment_terminal_authority(fixture, tamper=tamper)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal grade authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_evidence_commit_rejects_recommitted_source_terminal_authority(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    original = _load_fixture_source_terminal_commit(fixture)
    recommitted = _recommit_terminal_authority(
        fixture.committer_arguments["experiment_db_path"],
        original,
    )
    _assert_same_terminal_semantics(original, recommitted)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="source terminal authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_evidence_commit_resumes_idempotently_and_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    crashed = False

    def crash_after_staging(phase: str) -> None:
        nonlocal crashed
        if phase == "artifacts_staged" and not crashed:
            crashed = True
            raise SimulatedCrash("power loss after durable artifact staging")

    first = EvidenceCommitter(
        **fixture.committer_arguments,
        phase_observer=crash_after_staging,
    )
    with pytest.raises(SimulatedCrash, match="power loss"):
        first.commit(fixture.request)

    with sqlite3.connect(first.outbox_path) as conn:
        status, phase = conn.execute(
            """
            SELECT status, phase FROM evidence_commits
             WHERE commit_id=?
            """,
            (fixture.request.commit_id,),
        ).fetchone()
    assert (status, phase) == ("failed", "artifacts_staged")
    assert not [
        event
        for event in fixture.state.read_events_since(
            fixture.request.aggregate_run_id,
            limit=100,
        )
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]

    resumed = EvidenceCommitter(**fixture.committer_arguments)
    result = resumed.commit(fixture.request)

    assert result.status == "complete"
    assert result.phases == (
        "initialized",
        "grades_verified",
        "trace_persisted",
        "artifacts_staged",
        "manifest_appended",
        "checkpoints_persisted",
        "authoritatively_verified",
        "complete",
    )
    assert set(result.ledger_verifications) == {
        "aggregate-run",
        "runtime-run",
    }
    assert all(
        verification.valid
        and verification.truncation_checked
        and verification.authoritative_head_verified
        for verification in result.ledger_verifications.values()
    )
    assert result.trace_closure.ok
    assert result.projection["recognized_event_count"] == 7
    assert len(result.projection["executions"]) == 1

    roles = {str(artifact["role"]) for artifact in result.artifacts}
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
    } <= roles

    state_snapshot = next(
        artifact
        for artifact in result.artifacts
        if artifact["role"] == "state_snapshot"
    )
    state_snapshot_bytes = resumed.artifact_store.read_bytes(
        str(state_snapshot["digest"]["sha256"])
    )
    assert EVIDENCE_COMMIT_EVENT_KIND.encode("utf-8") not in state_snapshot_bytes

    manifest_events = [
        event
        for event in fixture.state.read_events_since(
            fixture.request.aggregate_run_id,
            limit=100,
        )
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]
    assert len(manifest_events) == 1
    assert manifest_events[0]["event_id"] == result.manifest_event_id
    assert (
        manifest_events[0]["payload"]["artifact_manifest_hash"]
        == result.artifact_manifest["manifest_hash"]
    )

    replay = resumed.commit(fixture.request)
    assert replay.manifest_event_id == result.manifest_event_id
    assert replay.manifest_event_hash == result.manifest_event_hash
    assert replay.projection_sha256 == result.projection_sha256
    assert len(
        resumed.checkpoint_store.load_all("aggregate-run")
    ) == 1
    assert len(
        resumed.checkpoint_store.load_all("runtime-run")
    ) == 1
    assert len(
        [
            event
            for event in fixture.state.read_events_since(
                fixture.request.aggregate_run_id,
                limit=100,
            )
            if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
        ]
    ) == 1

    conflicting = replace(
        fixture.request,
        subject={"mode": "hermetic", "changed": True},
    )
    with pytest.raises(
        EvidenceCommitConflict,
        match="different input",
    ):
        resumed.commit(conflicting)

    projection_path = (
        resumed.root
        / next(
            str(artifact["ref"])
            for artifact in result.artifacts
            if artifact["role"] == "tracer_projection"
        )
    )
    projection_bytes = projection_path.read_bytes()
    projection_path.unlink()
    repaired = resumed.commit(fixture.request)
    assert repaired.projection_sha256 == result.projection_sha256
    assert projection_path.read_bytes() == projection_bytes

    projection_path.write_bytes(b'{"tampered":true}')
    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="differs from CAS",
    ):
        resumed.commit(fixture.request)


def test_concurrent_resumptions_publish_one_manifest_event_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)

    def crash_after_staging(phase: str) -> None:
        if phase == "artifacts_staged":
            raise SimulatedCrash("power loss after durable artifact staging")

    with pytest.raises(SimulatedCrash, match="power loss"):
        EvidenceCommitter(
            **fixture.committer_arguments,
            phase_observer=crash_after_staging,
        ).commit(fixture.request)

    original_write_event = fixture.state.write_event
    simultaneous_publish = threading.Barrier(2)

    def synchronized_write_event(**kwargs: Any) -> int:
        if kwargs.get("kind") == EVIDENCE_COMMIT_EVENT_KIND:
            try:
                simultaneous_publish.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass
        return original_write_event(**kwargs)

    monkeypatch.setattr(
        fixture.state,
        "write_event",
        synchronized_write_event,
    )

    def resume() -> Any:
        return EvidenceCommitter(
            **fixture.committer_arguments
        ).commit(fixture.request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(resume) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    manifest_events = [
        event
        for event in fixture.state.read_events_since(
            fixture.request.aggregate_run_id,
            limit=100,
        )
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]
    assert len(manifest_events) == 1
    assert {
        (result.manifest_event_id, result.manifest_event_hash)
        for result in results
    } == {
        (
            manifest_events[0]["event_id"],
            manifest_events[0]["event_hash"],
        )
    }

    committer = EvidenceCommitter(**fixture.committer_arguments)
    with sqlite3.connect(committer.outbox_path) as conn:
        phase_rows = conn.execute(
            """
            SELECT detail_json
              FROM evidence_commit_phases
             WHERE commit_id=? AND phase='manifest_appended'
            """,
            (fixture.request.commit_id,),
        ).fetchall()
    assert len(phase_rows) == 1
    assert (
        json.loads(phase_rows[0][0])["event_id"]
        == manifest_events[0]["event_id"]
    )

    replay = committer.commit(fixture.request)
    assert replay.manifest_event_id == manifest_events[0]["event_id"]
    assert replay.manifest_event_hash == manifest_events[0]["event_hash"]
    assert all(
        verification.valid
        and verification.truncation_checked
        and verification.authoritative_head_verified
        for verification in replay.ledger_verifications.values()
    )


def test_crash_after_manifest_append_is_reconciled_without_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    original_write_event = fixture.state.write_event
    crashed = False

    def append_then_crash(**kwargs: Any) -> int:
        nonlocal crashed
        event_id = original_write_event(**kwargs)
        if (
            kwargs.get("kind") == EVIDENCE_COMMIT_EVENT_KIND
            and not crashed
        ):
            crashed = True
            raise SimulatedCrash("power loss after manifest append")
        return event_id

    monkeypatch.setattr(
        fixture.state,
        "write_event",
        append_then_crash,
    )

    with pytest.raises(SimulatedCrash, match="after manifest append"):
        EvidenceCommitter(
            **fixture.committer_arguments
        ).commit(fixture.request)

    before_resume = [
        event
        for event in fixture.state.read_events_since(
            fixture.request.aggregate_run_id,
            limit=100,
        )
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]
    assert len(before_resume) == 1

    resumed = EvidenceCommitter(
        **fixture.committer_arguments
    ).commit(fixture.request)
    after_resume = [
        event
        for event in fixture.state.read_events_since(
            fixture.request.aggregate_run_id,
            limit=100,
        )
        if event["kind"] == EVIDENCE_COMMIT_EVENT_KIND
    ]

    assert after_resume == before_resume
    assert resumed.manifest_event_id == before_resume[0]["event_id"]
    assert resumed.manifest_event_hash == before_resume[0]["event_hash"]
    assert resumed.status == "complete"


@pytest.mark.parametrize("swapped_level", (0, 1))
def test_evidence_write_parent_swap_cannot_escape_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swapped_level: int,
) -> None:
    root = tmp_path / "evidence"
    first = root / "checked-parent"
    second = first / "checked-child"
    second.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    relative_parts = ("checked-parent", "checked-child", "result.json")
    content = b"must remain under the opened evidence tree"

    swapped_parent = (first, second)[swapped_level]
    saved_parent = swapped_parent.with_name(
        f"{swapped_parent.name}-original"
    )
    if swapped_level == 0:
        (outside / "checked-child").mkdir()
        expected_inside = saved_parent / "checked-child" / "result.json"
        outside_target = outside / "checked-child" / "result.json"
    else:
        expected_inside = saved_parent / "result.json"
        outside_target = outside / "result.json"

    original_lstat = Path.lstat
    original_open = os.open
    swapped = False

    def swap_parent() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped_parent.rename(saved_parent)
        swapped_parent.symlink_to(outside, target_is_directory=True)
        swapped = True

    def racing_lstat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        metadata = original_lstat(path, *args, **kwargs)
        if path == swapped_parent:
            swap_parent()
        return metadata

    def racing_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        if (
            dir_fd is not None
            and path == swapped_parent.name
            and flags & getattr(os, "O_DIRECTORY", 0)
        ):
            swap_parent()
        return descriptor

    monkeypatch.setattr(Path, "lstat", racing_lstat)
    monkeypatch.setattr(os, "open", racing_open)

    _write_evidence_file(
        root,
        "/".join(relative_parts),
        content,
    )

    assert swapped
    assert not outside_target.exists()
    assert expected_inside.read_bytes() == content


def test_invalid_frozen_stream_writes_no_checkpoint_or_signature(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _CountingCheckpointAuthority()
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }
    fixture.state._conn.execute("DROP TRIGGER IF EXISTS events_no_update")
    fixture.state._conn.execute(
        "UPDATE events SET event_hash=? WHERE run_id=?",
        ("0" * 64, "runtime-run"),
    )
    fixture.state._conn.commit()

    committer = EvidenceCommitter(**arguments)
    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="frozen evidence stream verification failed",
    ):
        committer.commit(fixture.request)

    assert authority.sign_calls == 0
    assert committer.checkpoint_store.load_all("aggregate-run") == []
    assert committer.checkpoint_store.load_all("runtime-run") == []


def test_evidence_commit_requires_rollback_independent_checkpoint_pins(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _CountingCheckpointAuthority()
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }
    arguments.pop("trusted_checkpoint_pins")

    committer = EvidenceCommitter(**arguments)
    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="explicit trusted checkpoint pin store",
    ):
        committer.commit(fixture.request)

    assert authority.sign_calls == 0
    assert committer.checkpoint_store.load_all("aggregate-run") == []
    assert committer.checkpoint_store.load_all("runtime-run") == []


def test_checkpoint_crash_replay_does_not_resign_existing_randomized_checkpoint(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _RandomizedCheckpointAuthority(fail_on_sign_call=2)
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }

    first = EvidenceCommitter(**arguments)
    with pytest.raises(
        SimulatedCrash,
        match="checkpoint signing",
    ):
        first.commit(fixture.request)

    assert authority.sign_calls == 2
    assert len(first.checkpoint_store.load_all("runtime-run")) == 1
    assert first.checkpoint_store.load_all("aggregate-run") == []

    authority.fail_on_sign_call = None
    resumed = EvidenceCommitter(**arguments)
    result = resumed.commit(fixture.request)

    assert result.status == "complete"
    assert authority.sign_calls == 3
    assert len(resumed.checkpoint_store.load_all("runtime-run")) == 1
    assert len(resumed.checkpoint_store.load_all("aggregate-run")) == 1


def test_completed_commit_replay_uses_pinned_result_after_streams_append(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _CountingCheckpointAuthority()
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }
    first = EvidenceCommitter(**arguments)
    completed = first.commit(fixture.request)
    completed_sign_calls = authority.sign_calls

    fixture.state.write_event(
        run_id="aggregate-run",
        source="test",
        kind="post.commit.aggregate",
        payload={"after": "complete"},
        ts=500,
    )
    fixture.state.write_event(
        run_id="runtime-run",
        source="test",
        kind="post.commit.runtime",
        payload={"after": "complete"},
        ts=501,
    )

    replayed = EvidenceCommitter(**arguments).commit(fixture.request)

    assert authority.sign_calls == completed_sign_calls
    assert replayed.manifest_event_id == completed.manifest_event_id
    assert replayed.manifest_event_hash == completed.manifest_event_hash
    assert replayed.checkpoint_refs == completed.checkpoint_refs
    assert replayed.projection_sha256 == completed.projection_sha256
    assert {
        run_id: verification.event_count
        for run_id, verification in replayed.ledger_verifications.items()
    } == {
        run_id: verification.event_count
        for run_id, verification in completed.ledger_verifications.items()
    }


def test_crash_before_materialization_durability_resumes_after_state_change(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    crashed = EvidenceCommitter(**fixture.committer_arguments)

    def crash_before_store(*args: Any, **kwargs: Any) -> None:
        raise SimulatedCrash("power loss before materialization durability")

    crashed._store_materialization = crash_before_store  # type: ignore[method-assign]
    with pytest.raises(SimulatedCrash, match="power loss"):
        crashed.commit(fixture.request)

    fixture.state.register_run(
        run_id="unrelated-run",
        session_id="unrelated-session",
        rollout_path=str(tmp_path / "unrelated.jsonl"),
        task="unrelated post-crash state change",
        scope=ScopeContract(allowed_paths=(str(tmp_path),)),
        target_kind="hermetic",
        config_snapshot={
            "mode": "hermetic",
            "operational_efficacy_evidence": False,
        },
    )

    resumed = EvidenceCommitter(**fixture.committer_arguments)
    result = resumed.commit(fixture.request)

    assert result.status == "complete"
    assert result.trace_closure.ok
    replay = EvidenceCommitter(**fixture.committer_arguments).commit(
        fixture.request
    )
    assert replay.manifest_event_hash == result.manifest_event_hash
    assert replay.projection_sha256 == result.projection_sha256


def test_completed_commit_replay_survives_post_commit_grade_append(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    completed = EvidenceCommitter(**fixture.committer_arguments).commit(
        fixture.request
    )
    history = fixture.request.grade_histories[0]
    gradebook_path = fixture.committer_arguments["gradebook_path"]
    assert isinstance(gradebook_path, Path)
    regrade = Grade(
        verifier_id="fixture-hidden-verifier",
        verifier_version="v2",
        verifier_hash="3" * 64,
        frozen_result_hash=history.run.frozen_result_hash,
        passed=True,
        score=0.5,
        evidence={
            "mode": "hermetic",
            "operational_verifier": False,
        },
    )
    with GradeBook(gradebook_path) as gradebook:
        appended = gradebook.append_grade(
            run=history.run,
            grade=regrade,
            verifier_config_hash="8" * 64,
            supersedes_grade_id=history.revisions[-1].grade_id,
        )
        assert appended.grade_id not in {
            revision.grade_id for revision in history.revisions
        }

    replayed = EvidenceCommitter(**fixture.committer_arguments).commit(
        fixture.request
    )

    assert replayed.status == "complete"
    assert replayed.trace_closure.ok
    assert replayed.manifest_event_id == completed.manifest_event_id
    assert replayed.manifest_event_hash == completed.manifest_event_hash
    assert replayed.checkpoint_refs == completed.checkpoint_refs
    assert replayed.projection_sha256 == completed.projection_sha256


def test_completed_commit_replay_rejects_a_tampered_trace_store(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _CountingCheckpointAuthority()
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }
    EvidenceCommitter(**arguments).commit(fixture.request)
    trace_path = Path(arguments["trace_store_path"])

    with sqlite3.connect(trace_path) as connection:
        connection.execute("DROP TRIGGER trace_edges_no_delete")
        connection.execute(
            """
            DELETE FROM trace_edges
            WHERE edge_sequence = (
              SELECT MAX(edge_sequence) FROM trace_edges
            )
            """
        )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="persisted trace graph differs",
    ):
        EvidenceCommitter(**arguments).commit(fixture.request)


def test_completed_commit_replay_rejects_removed_terminal_arm_authority(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    EvidenceCommitter(**fixture.committer_arguments).commit(fixture.request)
    experiment_path = fixture.committer_arguments["experiment_db_path"]
    assert isinstance(experiment_path, Path)
    with sqlite3.connect(experiment_path) as connection:
        connection.execute("DROP TRIGGER experiment_arm_states_no_delete")
        connection.execute(
            """
            DELETE FROM experiment_arm_state_events
            WHERE state IN (
              'completed', 'failed', 'common_infrastructure_failed'
            )
            """
        )

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal grade authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


@pytest.mark.parametrize("tamper", ("row_identity", "terminal_state_hash"))
def test_completed_commit_replay_rejects_tampered_terminal_arm_authority(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    EvidenceCommitter(**fixture.committer_arguments).commit(fixture.request)
    _tamper_experiment_terminal_authority(fixture, tamper=tamper)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="terminal grade authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_completed_commit_replay_rejects_recommitted_gradebook_authority(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    EvidenceCommitter(**fixture.committer_arguments).commit(fixture.request)
    history = fixture.request.grade_histories[0]
    original = history.terminal_commits[0]
    gradebook_path = fixture.committer_arguments["gradebook_path"]
    assert isinstance(gradebook_path, Path)
    with sqlite3.connect(gradebook_path) as connection:
        connection.execute("DROP TRIGGER grade_terminal_commits_no_delete")
        connection.execute(
            "DELETE FROM grade_terminal_commits WHERE grade_id=?",
            (original.grade_id,),
        )
    with GradeBook(gradebook_path) as gradebook:
        recommitted = gradebook.commit_terminal_grade(
            grade_id=original.grade_id,
            revision_hash=original.grade_revision_hash,
            experiment_id=original.experiment_id,
            task_id=original.task_id,
            arm=original.arm,
            terminal_state=original.terminal_state,
            terminal_state_hash=original.terminal_state_hash,
        )

    assert recommitted.commit_id != original.commit_id
    assert recommitted.commit_hash != original.commit_hash
    assert (
        recommitted.experiment_id,
        recommitted.task_id,
        recommitted.arm,
        recommitted.terminal_state,
        recommitted.terminal_state_hash,
    ) == (
        original.experiment_id,
        original.task_id,
        original.arm,
        original.terminal_state,
        original.terminal_state_hash,
    )
    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="GradeBook history does not match the evidence request",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_completed_commit_replay_rejects_recommitted_source_authority(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    EvidenceCommitter(**fixture.committer_arguments).commit(fixture.request)
    original = _load_fixture_source_terminal_commit(fixture)
    recommitted = _recommit_terminal_authority(
        fixture.committer_arguments["experiment_db_path"],
        original,
    )
    _assert_same_terminal_semantics(original, recommitted)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="source terminal authority",
    ):
        EvidenceCommitter(**fixture.committer_arguments).commit(
            fixture.request
        )


def test_completed_commit_replay_requires_external_latest_checkpoint_to_exist_locally(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    authority = _CountingCheckpointAuthority()
    arguments = {
        **fixture.committer_arguments,
        "signer": authority,
        "verifier": authority,
    }
    committer = EvidenceCommitter(**arguments)
    completed = committer.commit(fixture.request)
    pin_store = fixture.committer_arguments["trusted_checkpoint_pins"]
    assert isinstance(pin_store, _IndependentCheckpointPins)

    newer_checkpoints = []
    for index, run_id in enumerate(fixture.request.registered_run_ids):
        fixture.state.write_event(
            run_id=run_id,
            source="test",
            kind="post.commit.checkpointed",
            payload={"run_id": run_id},
            ts=500 + index,
        )
        events = fixture.state.read_events_since(
            run_id,
            after_event_id=0,
            limit=100,
        )
        head = events[-1]
        persisted = committer.checkpoint_store.append_signed_head(
            run_id=run_id,
            head_event_id=head["event_id"],
            head_event_hash=head["event_hash"],
            event_count=len(events),
            signer=authority,
            verifier=authority,
            created_at=600 + index,
        )
        pin_store.pin(checkpoint_identity(persisted.checkpoint))
        newer_checkpoints.append(persisted)

    replayed = EvidenceCommitter(**arguments).commit(fixture.request)
    assert replayed.manifest_event_hash == completed.manifest_event_hash

    for persisted in newer_checkpoints:
        persisted.path.unlink()

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="rollback|latest checkpoint",
    ):
        EvidenceCommitter(**arguments).commit(fixture.request)


class _Fixture:
    def __init__(
        self,
        *,
        state: State,
        request: EvidenceCommitRequest,
        committer_arguments: dict[str, object],
    ) -> None:
        self.state = state
        self.request = request
        self.committer_arguments = committer_arguments


def _build_fixture(
    tmp_path: Path,
    *,
    mismatched_terminal_grade_reference: bool = False,
) -> _Fixture:
    state_path = tmp_path / "state.db"
    experiment_path = tmp_path / "experiments.db"
    gradebook_path = tmp_path / "grades.db"
    trace_path = tmp_path / "trace.db"
    evidence_root = tmp_path / "evidence"
    state = State(str(state_path))
    scope = ScopeContract(allowed_paths=(str(tmp_path),))
    for run_id in ("aggregate-run", "runtime-run"):
        state.register_run(
            run_id=run_id,
            session_id=f"{run_id}-session",
            rollout_path=str(tmp_path / f"{run_id}.jsonl"),
            task="hermetic evidence committer fixture",
            scope=scope,
            target_kind="hermetic",
            config_snapshot={
                "mode": "hermetic",
                "operational_efficacy_evidence": False,
            },
        )
    experiment_store = SqliteExperimentStore(experiment_path)

    frozen = FrozenTaskResult.create(
        task_id="fixture-task",
        task_family="generic",
        task_spec_hash="1" * 64,
        run_result_hash="2" * 64,
        patch="diff --git a/a b/a\n",
        output="hermetic",
        metadata={"mode": "hermetic"},
        frozen_at_ms=1_720_000_000_000,
    )
    run = RunEnvelopeRef.from_frozen_result(
        run_id="runtime-run",
        run_envelope_hash=canonical_revision_hash(
            {
                "run_id": "runtime-run",
                "frozen_result_hash": frozen.result_hash,
            }
        ),
        frozen_result=frozen,
    )
    grade = Grade(
        verifier_id="fixture-hidden-verifier",
        verifier_version="v1",
        verifier_hash="3" * 64,
        frozen_result_hash=frozen.result_hash,
        passed=True,
        score=1.0,
        evidence={
            "mode": "hermetic",
            "operational_verifier": False,
        },
    )
    with GradeBook(experiment_path) as authority_gradebook:
        authority_revision = authority_gradebook.append_grade(
            run=run,
            grade=grade,
            verifier_config_hash="6" * 64,
        )
        experiment_store.start_arm_attempt(
            experiment_id="fixture-experiment",
            task_id="fixture-task",
            block_attempt=0,
            arm=Arm.A,
            payload={
                "assignment_id": "assignment-1",
                "execution_id": "fixture-execution",
            },
            transition_idempotency_key="fixture.arm.A.started",
        )
        terminal_event = experiment_store.finish_arm_attempt(
            experiment_id="fixture-experiment",
            task_id="fixture-task",
            block_attempt=0,
            arm=Arm.A,
            state="completed",
            payload={
                "outcome": {
                    "arm": Arm.A.value,
                    "status": "completed",
                    "grade_revision": replace(
                        GradeRevisionRef.from_revision(
                            authority_revision
                        ),
                        grade_id="grade_unpublished",
                        revision_hash="f" * 64,
                    ).to_dict()
                    if mismatched_terminal_grade_reference
                    else GradeRevisionRef.from_revision(
                        authority_revision
                    ).to_dict(),
                },
            },
            transition_kind="arm.completed",
            transition_idempotency_key="fixture.arm.A.completed",
            transition_payload={
                "execution_id": "fixture-execution",
                "grade_revision_hash": authority_revision.revision_hash,
            },
        )
        source_terminal_commit = authority_gradebook.commit_terminal_grade(
            grade_id=authority_revision.grade_id,
            revision_hash=authority_revision.revision_hash,
            experiment_id="fixture-experiment",
            task_id="fixture-task",
            arm=Arm.A.value,
            terminal_state="completed",
            terminal_state_hash=str(terminal_event["state_hash"]),
        )

    with GradeBook(gradebook_path) as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=grade,
            verifier_config_hash="4" * 64,
        )
        gradebook.commit_terminal_grade(
            grade_id=first.grade_id,
            revision_hash=first.revision_hash,
            experiment_id="fixture-experiment",
            task_id="fixture-task",
            arm=Arm.A.value,
            terminal_state="completed",
            terminal_state_hash=str(terminal_event["state_hash"]),
        )
        second = gradebook.append_grade(
            run=run,
            grade=grade,
            verifier_config_hash="5" * 64,
            supersedes_grade_id=first.grade_id,
        )
        gradebook.commit_terminal_grade(
            grade_id=second.grade_id,
            revision_hash=second.revision_hash,
            experiment_id="fixture-experiment",
            task_id="fixture-task",
            arm=Arm.A.value,
            terminal_state="completed",
            terminal_state_hash=str(terminal_event["state_hash"]),
        )
        revisions = gradebook.list_revisions(run)
        invalidations = gradebook.list_invalidations(first.grade_id)
        terminal_commits = tuple(
            commit
            for revision in revisions
            if (
                commit := gradebook.get_terminal_commit(
                    revision.grade_id
                )
            )
            is not None
        )
    history = EvidenceGradeHistory(
        execution_id="fixture-execution",
        run=run,
        revisions=revisions,
        invalidations=invalidations,
        terminal_commits=terminal_commits,
        source_terminal_commits=(source_terminal_commit,),
    )

    graph, promotion = _closed_graph(
        run_envelope_hash=run.run_envelope_hash,
        result_hash=frozen.result_hash,
        revisions=revisions,
        invalidations=invalidations,
    )
    closure_time = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    with GradeBook(gradebook_path) as gradebook:
        closure = graph.validate_closure(
            now=closure_time,
            decision_grade_validator=gradebook,
        )
    assert closure.ok

    event_clock = 100

    def emit(kind: str, payload: dict[str, object]) -> None:
        nonlocal event_clock
        event_clock += 1
        state.write_event(
            run_id="aggregate-run",
            source="test",
            kind=kind,
            payload=payload,
            ts=event_clock,
        )

    state.write_event(
        run_id="runtime-run",
        source="test",
        kind="tracer.execution.completed",
        payload={
            "execution_id": "fixture-execution",
            "terminal": True,
        },
        ts=100,
    )
    emit(
        "tracer.submitted",
        {
            "mode": "hermetic",
            "operational_efficacy_evidence": False,
            "not_executed": ["provider"],
        },
    )
    emit(
        "tracer.matrix.frozen",
        {
            "execution_count": 1,
            "coordinates": [
                {
                    "task_family": "generic",
                    "runtime_kind": "codex",
                    "arm": "A",
                }
            ],
        },
    )
    emit(
        "tracer.assignment.persisted",
        {
            "experiment_id": "fixture-experiment",
            "task_id": "fixture-task",
            "task_family": "generic",
            "runtime_kind": "codex",
            "assignment_id": "assignment-1",
            "order": ["A"],
        },
    )
    emit(
        "tracer.execution.joined",
        {
            "execution_id": "fixture-execution",
            "experiment_id": "fixture-experiment",
            "task_id": "fixture-task",
            "task_family": "generic",
            "runtime_kind": "codex",
            "arm": "A",
            "assignment_id": "assignment-1",
            "runtime_run_id": "runtime-run",
            "runtime_session_id": "runtime-run-session",
            "original_frozen_result_hash": frozen.result_hash,
            "blinded_frozen_result_hash": frozen.result_hash,
            "grade_revision_hash": second.revision_hash,
        },
    )
    emit(
        "tracer.trace.closed",
        {
            "status": closure.to_dict()["status"],
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "promotion_trace": [
                node.identity.canonical_key
                for node in graph.promotion_trace(promotion)
            ],
        },
    )
    emit(
        "tracer.claim.authorized",
        {
            "max_claim_level": "L1",
            "l2_refusal": "fixture evidence has no independent verifier",
            "operational_efficacy_evidence": False,
            "improvement_claim_allowed": False,
        },
    )
    emit(
        "tracer.completed",
        {
            "execution_count": 1,
            "claim_cap": "L1",
            "mode": "hermetic",
            "external_provider_calls": 0,
        },
    )

    base_artifacts = (
        _json_artifact(
            "run_manifest",
            "artifacts/run-manifest.json",
            {"mode": "hermetic", "run_id": "aggregate-run"},
        ),
        _json_artifact(
            "execution_results",
            "artifacts/executions.json",
            {"execution_id": "fixture-execution"},
        ),
        _json_artifact(
            "trace_graph",
            "artifacts/trace-graph.json",
            {"graph": graph.to_dict(), "closure": closure.to_dict()},
        ),
        _json_artifact(
            "hidden_verifier_result",
            "artifacts/hidden-verifier-result.json",
            {"passed": True, "operational_verifier": False},
        ),
    )
    artifact_bytes = {
        artifact.relative_path: artifact.content
        for artifact in base_artifacts
    }
    descriptors = {
        artifact.role: {
            "ref": artifact.relative_path,
            "sha256": hashlib.sha256(artifact.content).hexdigest(),
        }
        for artifact in base_artifacts
    }
    evidence_bundle = {
        "pins": {
            "mode": "hermetic",
            "fixture": "evidence-committer",
        },
        "hashes": {
            "run_manifest_sha256": descriptors["run_manifest"]["sha256"],
            "execution_results_sha256": descriptors[
                "execution_results"
            ]["sha256"],
        },
        "artifacts": list(descriptors.values()),
        "traceable_detector": {
            "detector_id": "fixture-trace-closure/v1",
            "trace_ref": descriptors["trace_graph"]["ref"],
            "trace_sha256": descriptors["trace_graph"]["sha256"],
        },
    }
    report = ClaimGate.derive_report(
        {
            "schema_version": "fixture-claim-report/v1",
            "mode": "hermetic",
            "asserted_claim_level": "L1",
            "claims": [
                "CLAIM-HARNESS-L0-INTEGRITY",
                "CLAIM-HARNESS-L1-PROCESS",
            ],
            "operational_efficacy_evidence": False,
        },
        evidence_bundle,
        evidence_resolver=artifact_bytes.get,
    )
    artifacts = (
        *base_artifacts,
        _json_artifact(
            "claim_evidence_bundle",
            "artifacts/claim-evidence-bundle.json",
            evidence_bundle,
        ),
        _json_artifact(
            "claim_report",
            "artifacts/claim-report.json",
            report,
        ),
    )
    request = EvidenceCommitRequest(
        commit_id="fixture-evidence-commit",
        aggregate_run_id="aggregate-run",
        registered_run_ids=("aggregate-run", "runtime-run"),
        mode="hermetic",
        claim_cap="L1",
        operational_efficacy_evidence=False,
        subject={
            "mode": "hermetic",
            "execution_count": 1,
            "operational_efficacy_evidence": False,
        },
        grade_histories=(history,),
        trace_graph=graph,
        promotion=promotion,
        closure_time=closure_time,
        artifacts=artifacts,
        manifest_event_ts=200,
        checkpoint_created_at=300,
    )
    authority = HmacCheckpointAuthority(
        key_id="fixture-hermetic-key",
        key=b"fixture-hermetic-checkpoint-key",
    )
    trusted_checkpoint_pins = _IndependentCheckpointPins()
    return _Fixture(
        state=state,
        request=request,
        committer_arguments={
            "root": evidence_root,
            "state": state,
            "experiment_db_path": experiment_path,
            "gradebook_path": gradebook_path,
            "trace_store_path": trace_path,
            "signer": authority,
            "verifier": authority,
            "trusted_checkpoint_pins": trusted_checkpoint_pins,
        },
    )


def _json_artifact(
    role: str,
    relative_path: str,
    payload: object,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        role=role,
        relative_path=relative_path,
        content=canonical_json_bytes(payload),
    )


def _tamper_experiment_terminal_authority(
    fixture: _Fixture,
    *,
    tamper: str,
) -> None:
    experiment_path = fixture.committer_arguments["experiment_db_path"]
    assert isinstance(experiment_path, Path)
    with sqlite3.connect(experiment_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP TRIGGER experiment_arm_states_no_update")
        rows = connection.execute(
            """
            SELECT *
            FROM experiment_arm_state_events
            ORDER BY arm_state_sequence
            """
        ).fetchall()
        previous_state_hash: str | None = None
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            task_id = str(row["task_id"])
            if tamper == "row_identity":
                task_id = "mismatched-fixture-task"
            elif (
                tamper == "terminal_state_hash"
                and str(row["state"]) == "completed"
            ):
                payload["tampered"] = True
            body = {
                "experiment_id": str(row["experiment_id"]),
                "task_id": task_id,
                "block_attempt": int(row["block_attempt"]),
                "arm": str(row["arm"]),
                "state": str(row["state"]),
                "payload": payload,
                "previous_state_hash": previous_state_hash,
                "recorded_at_ms": int(row["recorded_at_ms"]),
            }
            state_hash = canonical_revision_hash(body)
            connection.execute(
                """
                UPDATE experiment_arm_state_events
                SET task_id=?, payload_json=?, previous_state_hash=?,
                    state_hash=?
                WHERE arm_state_sequence=?
                """,
                (
                    task_id,
                    canonical_json_bytes(payload).decode("utf-8"),
                    previous_state_hash,
                    state_hash,
                    int(row["arm_state_sequence"]),
                ),
            )
            previous_state_hash = state_hash


def _load_fixture_source_terminal_commit(
    fixture: _Fixture,
) -> GradeTerminalCommit:
    experiment_path = fixture.committer_arguments["experiment_db_path"]
    assert isinstance(experiment_path, Path)
    [terminal_event] = SqliteExperimentStore(
        experiment_path
    ).list_terminal_arm_events()
    grade_reference = terminal_event["payload"]["outcome"][
        "grade_revision"
    ]
    with GradeBook(experiment_path) as gradebook:
        commit = gradebook.get_terminal_commit(
            str(grade_reference["grade_id"])
        )
    assert commit is not None
    return commit


def _recommit_terminal_authority(
    raw_path: object,
    original: GradeTerminalCommit,
) -> GradeTerminalCommit:
    assert isinstance(raw_path, Path)
    with sqlite3.connect(raw_path) as connection:
        connection.execute("DROP TRIGGER grade_terminal_commits_no_delete")
        connection.execute(
            "DELETE FROM grade_terminal_commits WHERE grade_id=?",
            (original.grade_id,),
        )
    with GradeBook(raw_path) as gradebook:
        recommitted = gradebook.commit_terminal_grade(
            grade_id=original.grade_id,
            revision_hash=original.grade_revision_hash,
            experiment_id=original.experiment_id,
            task_id=original.task_id,
            arm=original.arm,
            terminal_state=original.terminal_state,
            terminal_state_hash=original.terminal_state_hash,
        )
    assert recommitted.commit_id != original.commit_id
    assert recommitted.commit_hash != original.commit_hash
    return recommitted


def _assert_same_terminal_semantics(
    first: GradeTerminalCommit,
    second: GradeTerminalCommit,
) -> None:
    assert (
        second.grade_id,
        second.grade_revision_hash,
        second.experiment_id,
        second.task_id,
        second.arm,
        second.terminal_state,
        second.terminal_state_hash,
    ) == (
        first.grade_id,
        first.grade_revision_hash,
        first.experiment_id,
        first.task_id,
        first.arm,
        first.terminal_state,
        first.terminal_state_hash,
    )


def _rebind_fixture_claim_authority(
    artifacts: tuple[EvidenceArtifact, ...],
) -> tuple[EvidenceArtifact, ...]:
    bundle_artifact = next(
        artifact
        for artifact in artifacts
        if artifact.role == "claim_evidence_bundle"
    )
    report_artifact = next(
        artifact
        for artifact in artifacts
        if artifact.role == "claim_report"
    )
    bundle = json.loads(bundle_artifact.content)
    report_body = json.loads(report_artifact.content)
    report_body.pop("claim_gate", None)
    report_body.pop("improvement_claim_allowed", None)
    report_body.pop("powered_improvement_claim_allowed", None)
    artifact_bytes = {
        artifact.relative_path: artifact.content
        for artifact in artifacts
        if artifact.role not in {"claim_evidence_bundle", "claim_report"}
    }
    digest_replacements: dict[str, str] = {}
    for descriptor in bundle["artifacts"]:
        reference = str(descriptor["ref"])
        payload = artifact_bytes[reference]
        updated_digest = hashlib.sha256(payload).hexdigest()
        digest_replacements[str(descriptor["sha256"])] = updated_digest
        descriptor["sha256"] = updated_digest

    def replace_digests(value):
        if isinstance(value, dict):
            return {
                key: replace_digests(nested)
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [replace_digests(nested) for nested in value]
        if isinstance(value, str):
            return digest_replacements.get(value, value)
        return value

    bundle = replace_digests(bundle)
    report = ClaimGate.derive_report(
        report_body,
        bundle,
        evidence_resolver=artifact_bytes.get,
    )
    return tuple(
        replace(
            artifact,
            content=canonical_json_bytes(bundle),
        )
        if artifact.role == "claim_evidence_bundle"
        else replace(
            artifact,
            content=canonical_json_bytes(report),
        )
        if artifact.role == "claim_report"
        else artifact
        for artifact in artifacts
    )


def _closed_graph(
    *,
    run_envelope_hash: str,
    result_hash: str,
    revisions: tuple[Any, ...],
    invalidations: tuple[Any, ...],
) -> tuple[TraceGraph, TraceIdentity]:
    objective = _node(NodeType.OBJ, "OBJ", {"goal": "hermetic evidence"})
    requirement = _node(NodeType.REQ, "REQ", {"requirement": "traceability"})
    test = _node(NodeType.TEST, "TEST", {"test": "evidence committer"})
    assignment = _node(NodeType.ASN, "ASN", {"assignment": "fixture"})
    run = _node(
        NodeType.RUN,
        "RUN",
        run_envelope_hash,
        pinned=True,
        attributes={"runtime_run_id": "runtime-run"},
    )
    artifact = _node(
        NodeType.ART,
        "ART",
        result_hash,
        runtime_evidence=True,
        attributes={"frozen_result_hash": result_hash},
    )
    grade_nodes = {
        revision.grade_id: _node(
            NodeType.GRADE,
            f"GRADE-{revision.revision_number}",
            revision.revision_hash,
            verifier_id=revision.verifier_id,
            verifier_revision_hash=(
                revision.verifier_implementation_hash
            ),
            attributes={
                "record_kind": "grade_revision",
                **revision.to_dict(),
            },
        )
        for revision in revisions
    }
    invalidation_nodes = {
        invalidation.invalidation_id: _node(
            NodeType.GRADE,
            f"GRADE-INVALIDATION-{invalidation.invalidation_id}",
            invalidation.invalidation_hash,
            verifier_id=grade_nodes[
                invalidation.grade_id
            ].verifier_id,
            verifier_revision_hash=grade_nodes[
                invalidation.grade_id
            ].verifier_revision_hash,
            attributes={
                "record_kind": "grade_invalidation",
                **invalidation.to_dict(),
            },
        )
        for invalidation in invalidations
    }
    current_grade = grade_nodes[revisions[-1].grade_id]
    analysis = _node(NodeType.ANL, "ANL", {"claim_cap": "L1"})
    decision = _node(
        NodeType.DEC,
        "DEC",
        {"decision": "publish hermetic"},
        attributes={
            "grade_citations": [
                {
                    "grade_id": revisions[-1].grade_id,
                    "revision_hash": revisions[-1].revision_hash,
                    "acknowledged_invalidation_hashes": [],
                    "resolution_grade_id": None,
                    "resolution_revision_hash": None,
                }
            ],
        },
    )
    promotion = _node(
        NodeType.PROMOTION,
        "PROMOTION",
        {"scope": "hermetic"},
    )
    graph = TraceGraph(
        nodes=(
            objective,
            requirement,
            test,
            assignment,
            run,
            artifact,
            *grade_nodes.values(),
            *invalidation_nodes.values(),
            analysis,
            decision,
            promotion,
        ),
        edges=(
            TraceEdge(
                requirement.identity,
                EdgeType.IMPLEMENTS,
                objective.identity,
            ),
            TraceEdge(test.identity, EdgeType.TESTS, requirement.identity),
            TraceEdge(assignment.identity, EdgeType.SUPPORTS, test.identity),
            TraceEdge(run.identity, EdgeType.ASSIGNED_BY, assignment.identity),
            TraceEdge(
                artifact.identity,
                EdgeType.DERIVED_FROM,
                run.identity,
            ),
            *(
                TraceEdge(
                    grade.identity,
                    EdgeType.EVALUATES,
                    artifact.identity,
                )
                for grade in grade_nodes.values()
            ),
            *(
                TraceEdge(
                    grade_nodes[revision.grade_id].identity,
                    EdgeType.SUPERSEDES,
                    grade_nodes[revision.supersedes_grade_id].identity,
                )
                for revision in revisions
                if revision.supersedes_grade_id is not None
            ),
            *(
                TraceEdge(
                    invalidation_nodes[
                        invalidation.invalidation_id
                    ].identity,
                    EdgeType.INVALIDATES,
                    grade_nodes[invalidation.grade_id].identity,
                )
                for invalidation in invalidations
            ),
            TraceEdge(
                analysis.identity,
                EdgeType.DERIVED_FROM,
                current_grade.identity,
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
    return graph, promotion.identity


def _node(
    node_type: NodeType,
    logical_id: str,
    revision: object,
    *,
    pinned: bool = False,
    runtime_evidence: bool = False,
    verifier_id: str | None = None,
    verifier_revision_hash: str | None = None,
    attributes: dict[str, object] | None = None,
) -> TraceNode:
    revision_hash = (
        revision
        if isinstance(revision, str) and len(revision) == 64
        else canonical_revision_hash(revision)
    )
    identity = TraceIdentity(
        namespace="test/evidence-committer",
        node_type=node_type,
        logical_id=logical_id,
        revision_hash=revision_hash,
        instance_id=trace_instance_id_from_hash(
            timestamp_ms=1_720_000_000_000,
            content_hash=canonical_revision_hash(
                {
                    "node_type": node_type.value,
                    "logical_id": logical_id,
                    "revision_hash": revision_hash,
                }
            ),
            domain="test/evidence-committer",
        ),
    )
    return TraceNode(
        identity=identity,
        pinned=pinned,
        runtime_evidence=runtime_evidence,
        verifier_id=verifier_id,
        verifier_revision_hash=verifier_revision_hash,
        attributes=attributes or {},
    )
