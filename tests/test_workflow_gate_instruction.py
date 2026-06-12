from mcp_tools.codex_supervisor_stdio import _workflow_gate_instruction


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
