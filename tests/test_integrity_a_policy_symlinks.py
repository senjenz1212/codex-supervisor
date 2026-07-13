from __future__ import annotations

from hashlib import sha256

import pytest

from supervisor.autoresearch.policy_evolution import approve_policy_proposal
from supervisor.policy_overlay import (
    POLICY_OVERLAY_PATH,
    PolicyOverlayError,
    normalise_overlay_target,
    remove_repo_file_no_follow,
)
from supervisor.state import State


def test_normalise_overlay_target_rejects_symlinked_parent_directory(tmp_path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    (repo_root / ".supervisor").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PolicyOverlayError, match="symlink"):
        normalise_overlay_target(POLICY_OVERLAY_PATH, repo_root=repo_root)


def test_policy_approval_rejects_symlinked_backup_directory_before_writing(tmp_path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    target = repo_root / POLICY_OVERLAY_PATH
    candidate = repo_root / "candidates" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    outside.mkdir()
    original_bytes = b"schema_version: supervisor-policy-overlay/v1\n"
    candidate_bytes = (
        b"schema_version: supervisor-policy-overlay/v1\n"
        b"active_proposal_id: proposal-symlink\n"
    )
    target.write_bytes(original_bytes)
    candidate.write_bytes(candidate_bytes)
    (repo_root / ".handoff").mkdir()
    (repo_root / ".handoff" / "policy-rollbacks").symlink_to(
        outside,
        target_is_directory=True,
    )
    proposal = {
        "proposal_id": "proposal-symlink",
        "changes": [{
            "target_path": POLICY_OVERLAY_PATH,
            "candidate_ref": "candidates/policy-overlay.yaml",
            "before_hash": sha256(original_bytes).hexdigest(),
            "after_hash": sha256(candidate_bytes).hexdigest(),
        }],
    }
    state = State(str(tmp_path / "state.db"))

    with pytest.raises(PolicyOverlayError, match="symlink"):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=repo_root,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_bytes() == original_bytes
    assert list(outside.iterdir()) == []
    assert state.read_events_since("policy-run", after_event_id=0, limit=20) == []


def test_policy_approval_rejects_symlinked_overlay_file_before_writing(tmp_path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    overlay_dir = repo_root / ".supervisor"
    candidate = repo_root / "candidates" / "policy-overlay.yaml"
    overlay_dir.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    outside.mkdir()
    outside_target = outside / "policy-overlay.yaml"
    original_bytes = b"schema_version: supervisor-policy-overlay/v1\n"
    candidate_bytes = (
        b"schema_version: supervisor-policy-overlay/v1\n"
        b"active_proposal_id: proposal-target-symlink\n"
    )
    outside_target.write_bytes(original_bytes)
    (repo_root / POLICY_OVERLAY_PATH).symlink_to(outside_target)
    candidate.write_bytes(candidate_bytes)
    proposal = {
        "proposal_id": "proposal-target-symlink",
        "changes": [{
            "target_path": POLICY_OVERLAY_PATH,
            "candidate_ref": "candidates/policy-overlay.yaml",
            "before_hash": sha256(original_bytes).hexdigest(),
            "after_hash": sha256(candidate_bytes).hexdigest(),
        }],
    }
    state = State(str(tmp_path / "state.db"))

    with pytest.raises(PolicyOverlayError, match="symlink"):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=repo_root,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert outside_target.read_bytes() == original_bytes
    assert not (repo_root / ".handoff").exists()
    assert state.read_events_since("policy-run", after_event_id=0, limit=20) == []


def test_symlink_swap_cannot_redirect_cleanup_unlink_outside_repo(tmp_path):
    repo_root = tmp_path / "repo"
    overlay_parent = repo_root / ".supervisor"
    overlay_parent.mkdir(parents=True)
    target = repo_root / POLICY_OVERLAY_PATH
    target.write_bytes(b"temporary in-repo bytes")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.write_bytes(b"outside must survive")

    original_parent = repo_root / ".supervisor-original"
    overlay_parent.rename(original_parent)
    overlay_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PolicyOverlayError, match="symlink"):
        remove_repo_file_no_follow(
            target,
            repo_root=repo_root,
            label="policy overlay target",
        )

    assert outside_target.read_bytes() == b"outside must survive"
    assert (original_parent / target.name).read_bytes() == b"temporary in-repo bytes"
