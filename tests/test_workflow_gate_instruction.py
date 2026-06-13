from mcp_tools.codex_supervisor_stdio import (
    _workflow_gate_instruction,
    _workflow_round_objection,
    _workflow_tdd_test_names,
)


def test_execution_gate_instruction_is_implementation_framed():
    instruction = _workflow_gate_instruction(
        gate="execution",
        intent="ship the slice",
        corrective_context="",
    )

    assert "Implement this gate" in instruction
    assert "Edit the worktree" in instruction
    assert "Review this gate" not in instruction


def test_review_gate_instruction_keeps_review_framing():
    instruction = _workflow_gate_instruction(
        gate="implementation_plan",
        intent="check the plan",
        corrective_context="",
    )

    assert "Review this gate" in instruction
    assert "accept, revise, or deny" in instruction


def test_execution_gate_instruction_includes_tdd_runtime_contract():
    instruction = _workflow_gate_instruction(
        gate="outcome_review",
        intent="ship the slice",
        corrective_context="",
        tdd_test_names=["test_required_one", "test_required_two"],
    )

    assert "Runtime TDD test contract" in instruction
    assert "outcome.tests" in instruction
    assert "`accept`, `revise`, or `deny`" in instruction
    assert "Do not return `accept_with_residual`" in instruction
    assert "let the supervisor runtime floor rerun them" in instruction
    assert "- test_required_one" in instruction
    assert "- test_required_two" in instruction


def test_workflow_tdd_test_names_reads_tdd_artifacts(tmp_path):
    tdd = tmp_path / "docs" / "dual-agent" / "task" / "source" / "tdd.md"
    tdd.parent.mkdir(parents=True)
    tdd.write_text(
        "## Test Cases\n\n"
        "### test_required_one\n\n"
        "RED: fail\nGREEN: pass\n\n"
        "### test_required_two\n\n"
        "RED: fail\nGREEN: pass\n",
        encoding="utf-8",
    )

    assert _workflow_tdd_test_names(
        tmp_path,
        [{"kind": "tdd_plan", "path": str(tdd.relative_to(tmp_path))}],
    ) == ["test_required_one", "test_required_two"]


def test_workflow_round_objection_preserves_runtime_probe_details():
    objection = _workflow_round_objection(
        payload={"status": "accepted"},
        runtime_probe={
            "status": "red",
            "reason": "runtime_evidence_failed",
            "details": {
                "failures": ["tdd_tests_not_executed"],
                "tdd_test_coverage": {
                    "missing_nodeids": [
                        "tests/test_example.py::test_required_one",
                        "tests/test_example.py::test_required_two",
                    ],
                },
            },
        },
        deliverable_probe=None,
        claim_probe=None,
        cursor_review={"accepted": True},
        round_index=2,
        max_rounds=4,
    )

    assert objection.startswith("runtime_evidence_failed")
    assert "tdd_tests_not_executed" in objection
    assert "missing_nodeids[2]" in objection
    assert "tests/test_example.py::test_required_one" in objection
    assert "agents have not both accepted" not in objection
