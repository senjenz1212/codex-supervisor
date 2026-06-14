from __future__ import annotations

from hashlib import sha256

import pytest

from supervisor.review_packets import (
    ChangedFile,
    PlanningRef,
    ReceiptRef,
    ReviewerContextReceipt,
    build_review_packet,
    context_validation_payload,
    packet_includes_implementer_transcript,
    read_only_changed_file_contents,
    review_context_incomplete_reason,
    review_packet_event_kind,
    validate_reviewer_context_receipt,
    validate_review_packet,
)


def _packet():
    return build_review_packet(
        task_id="packet-task",
        run_id="run-1",
        gate="outcome_review",
        packet_id="review-packet-outcome_review-1",
        base_head="a" * 40,
        candidate_head="b" * 40,
        patch_hash="c" * 64,
        planning_refs=[
            PlanningRef(kind="prd", path="docs/dual-agent/packet-task/prd.md", sha256="1" * 64),
            PlanningRef(kind="tdd_plan", path="docs/dual-agent/packet-task/tdd.md", sha256="2" * 64),
        ],
        acceptance_items=["test_runtime_floor", "test_reviewer_context"],
        diff_refs=["runtime-git-diff-outcome_review-1"],
        name_status_refs=["runtime-git-diff-outcome_review-1:name_status"],
        changed_files=[
            ChangedFile(path="supervisor/review_packets.py", status="A", sha256="3" * 64),
            ChangedFile(path="tests/test_reviewer_packets.py", status="A", sha256="4" * 64),
        ],
        runtime_receipt_ids=[
            ReceiptRef(receipt_id="runtime-git-diff-outcome_review-1", kind="git_diff"),
            ReceiptRef(receipt_id="runtime-tests-outcome_review-1", kind="test"),
        ],
        declared_tests=["tests/test_reviewer_packets.py::test_review_packet_hash_is_stable"],
        executed_test_receipt_ids=[
            ReceiptRef(receipt_id="runtime-tests-outcome_review-1", kind="test")
        ],
        policy_overlay_hash="overlay-sha",
        lesson_hashes=["lesson-sha"],
        reviewer_ids=["independent-reviewer-0", "independent-reviewer-1"],
    )


def test_review_packet_includes_core_refs_and_stable_sha256():
    first = _packet()
    second = build_review_packet(
        **{
            **{
                "task_id": first.task_id,
                "run_id": first.run_id,
                "gate": first.gate,
                "packet_id": first.packet_id,
                "base_head": first.base_head,
                "candidate_head": first.candidate_head,
                "patch_hash": first.patch_hash,
            },
            "planning_refs": list(reversed(first.planning_refs)),
            "acceptance_items": list(reversed(first.acceptance_items)),
            "diff_refs": list(first.diff_refs),
            "name_status_refs": list(first.name_status_refs),
            "changed_files": list(reversed(first.changed_files)),
            "runtime_receipt_ids": list(reversed(first.runtime_receipt_ids)),
            "declared_tests": list(first.declared_tests),
            "executed_test_receipt_ids": list(first.executed_test_receipt_ids),
            "policy_overlay_hash": first.policy_overlay_hash,
            "lesson_hashes": list(first.lesson_hashes),
            "reviewer_ids": list(first.reviewer_ids),
        }
    )

    assert first.packet_sha256
    assert first.packet_sha256 == second.packet_sha256
    payload = first.to_event_payload()
    assert payload["planning_refs"][0]["kind"] == "prd"
    assert payload["changed_files"][0]["path"] == "supervisor/review_packets.py"
    assert payload["runtime_receipt_ids"][0]["receipt_id"] == "runtime-git-diff-outcome_review-1"
    assert payload["packet_sha256"] == first.packet_sha256


def test_review_packet_validation_rejects_missing_changed_file():
    packet = build_review_packet(
        task_id="packet-task",
        run_id="run-1",
        gate="outcome_review",
        packet_id="review-packet-outcome_review-1",
        base_head="a" * 40,
        changed_files=[ChangedFile(path="supervisor/review_packets.py", status="A")],
        acceptance_items=["test_runtime_floor", "test_reviewer_context"],
    )

    failures = validate_review_packet(
        packet,
        expected_changed_files=[
            "supervisor/review_packets.py",
            "tests/test_reviewer_packets.py",
        ],
        expected_acceptance_items=["test_runtime_floor", "test_reviewer_context"],
    )

    assert [failure.reason for failure in failures] == [
        "review_packet_changed_file_missing",
    ]
    assert failures[0].detail == "tests/test_reviewer_packets.py"


def test_review_packet_validation_rejects_missing_acceptance_item():
    packet = build_review_packet(
        task_id="packet-task",
        run_id="run-1",
        gate="outcome_review",
        packet_id="review-packet-outcome_review-1",
        base_head="a" * 40,
        changed_files=[ChangedFile(path="supervisor/review_packets.py", status="A")],
        acceptance_items=["test_runtime_floor"],
    )

    failures = validate_review_packet(
        packet,
        expected_changed_files=["supervisor/review_packets.py"],
        expected_acceptance_items=["test_runtime_floor", "test_reviewer_context"],
    )

    assert [failure.reason for failure in failures] == [
        "review_packet_acceptance_item_missing",
    ]
    assert failures[0].detail == "test_reviewer_context"


def test_review_packet_validation_rejects_missing_declared_test_and_forged_runtime_receipt():
    packet = build_review_packet(
        task_id="packet-task",
        run_id="run-1",
        gate="outcome_review",
        packet_id="review-packet-outcome_review-1",
        base_head="a" * 40,
        runtime_receipt_ids=[ReceiptRef(receipt_id="caller-forged-runtime", kind="test")],
        declared_tests=["tests/test_reviewer_packets.py::test_one"],
    )

    failures = validate_review_packet(
        packet,
        expected_changed_files=[],
        expected_acceptance_items=[],
        expected_declared_tests=[
            "tests/test_reviewer_packets.py::test_one",
            "tests/test_reviewer_packets.py::test_two",
        ],
        supervisor_runtime_receipt_ids=["runtime-tests-outcome_review-1"],
    )

    assert [failure.reason for failure in failures] == [
        "review_packet_declared_test_missing",
        "review_packet_runtime_receipt_not_supervisor_originated",
    ]


def test_reviewer_context_receipt_missing_changed_file_reports_incomplete():
    packet = _packet()
    receipt = ReviewerContextReceipt(
        reviewer_id="independent-reviewer-0",
        files_reviewed=["supervisor/review_packets.py"],
        criteria_checked=["test_runtime_floor", "test_reviewer_context"],
        receipts_considered=[
            "runtime-git-diff-outcome_review-1",
            "runtime-tests-outcome_review-1",
        ],
    )

    validation = validate_reviewer_context_receipt(packet, receipt)

    assert validation.complete is False
    assert validation.incomplete_reason == review_context_incomplete_reason()
    assert validation.missing_changed_files == ["tests/test_reviewer_packets.py"]
    payload = context_validation_payload(
        packet=packet,
        reviewer_id=receipt.reviewer_id,
        receipt=receipt,
        validation=validation,
    )
    assert payload["complete"] is False
    assert payload["reason"] == "review_context_incomplete"


def test_review_packet_excludes_implementer_transcript_by_default():
    packet = _packet()

    assert packet.implementer_transcript_ref is None
    assert packet_includes_implementer_transcript(packet) is False


def test_review_packet_event_created_for_reviewer_invocation():
    packet = _packet()
    payload = packet.to_event_payload()

    assert review_packet_event_kind() == "supervisor_review_packet_created"
    assert payload["packet_id"] == packet.packet_id
    assert payload["packet_sha256"] == packet.packet_sha256
    assert payload["task_id"] == "packet-task"
    assert payload["gate"] == "outcome_review"
    assert payload["reviewer_ids"] == [
        "independent-reviewer-0",
        "independent-reviewer-1",
    ]
    assert any(
        ref["receipt_id"] == "runtime-git-diff-outcome_review-1"
        for ref in payload["runtime_receipt_ids"]
    )
    assert "implementer_transcript_ref" not in payload or payload[
        "implementer_transcript_ref"
    ] is None


def test_reviewer_packet_exposes_read_only_changed_file_contents():
    contents = "print('packet')\n"
    packet = build_review_packet(
        task_id="packet-task",
        run_id="run-1",
        gate="outcome_review",
        packet_id="review-packet-outcome_review-1",
        base_head="a" * 40,
        changed_files=[
            ChangedFile(
                path="supervisor/review_packets.py",
                status="A",
                sha256=sha256(contents.encode("utf-8")).hexdigest(),
            )
        ],
    )

    assert read_only_changed_file_contents(
        packet,
        "supervisor/review_packets.py",
        contents_reader=lambda _path: contents,
    ) == contents

    with pytest.raises(ValueError):
        read_only_changed_file_contents(
            packet,
            "supervisor/review_packets.py",
            contents_reader=lambda _path: "tampered\n",
        )
