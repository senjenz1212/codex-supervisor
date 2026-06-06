from __future__ import annotations

import json
from pathlib import Path

from supervisor.dual_agent import (
    BOUNDED_TEST_FOLLOWUP,
    CredentialProbeInput,
    GateEvent,
    GateRound,
    Outcome,
    ProbeResult,
    WorkerInvocationProbeInput,
    WorktreeProbeInput,
    evaluate_artifact_redaction,
    evaluate_credential_boundary,
    evaluate_deadlock_budget,
    evaluate_outcome_fidelity,
    evaluate_outcome_gate_decision,
    evaluate_test_coverage_gate,
    evaluate_worker_invocation,
    evaluate_worktree_boundary,
    plan_telegram_notifications,
    slice0_hard_stop_summary,
    validate_parallel_isolation,
    verify_outcome_claims,
)
from supervisor.state import State


def test_p0_credential_boundary_checks_gateway_precedence_and_redaction():
    redaction_fixture = json.loads(Path(
        "tests/fixtures/dual_agent/p0_env_probe/expected_redactions.json"
    ).read_text())
    result = evaluate_credential_boundary(CredentialProbeInput(
        explicit_env={
            "ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com",
            "ANTHROPIC_AUTH_TOKEN": "sk-ant-explicit-secret",
        },
        ambient_env={
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_AUTH_TOKEN": "sk-ant-ambient-secret",
        },
        reported_request_host="uai-litellm.internal.unity.com",
        intended_gateway_host="https://uai-litellm.internal.unity.com",
        public_artifacts={
            "stderr": "ANTHROPIC_AUTH_TOKEN=sk-ant-explicit-secret",
            "outcome": "Authorization: Bearer secret-token",
        },
        required_env_keys=("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"),
    ))

    assert result.ok
    assert result.details["effective_keys"] == [
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ]
    assert redaction_fixture["must_not_publish"]


def test_p0_fails_when_successful_model_call_hits_wrong_gateway():
    result = evaluate_credential_boundary(CredentialProbeInput(
        explicit_env={"ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com"},
        ambient_env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
        reported_request_host="api.anthropic.com",
        intended_gateway_host="uai-litellm.internal.unity.com",
        public_artifacts={},
    ))

    assert not result.ok
    assert result.reason == "wrong_gateway"


def test_p0_fails_without_required_spawn_env_evidence():
    result = evaluate_credential_boundary(CredentialProbeInput(
        explicit_env={},
        ambient_env={"ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com"},
        reported_request_host="uai-litellm.internal.unity.com",
        intended_gateway_host="uai-litellm.internal.unity.com",
        public_artifacts={},
    ))

    assert not result.ok
    assert result.reason == "missing_required_env"
    assert result.details["missing"] == ["ANTHROPIC_BASE_URL"]


def test_p1_worktree_boundary_requires_expected_cwd_and_no_offlimits_touch(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    outcome = approved / "outcome.md"
    code = approved / "src" / "app.py"
    sentinel = tmp_path / "other" / "sentinel.txt"

    result = evaluate_worktree_boundary(WorktreeProbeInput(
        approved_cwd=str(approved),
        process_cwd=str(approved),
        outcome_path=str(outcome),
        touched_paths=(str(outcome), str(code)),
        off_limits_paths=(str(sentinel),),
        expected_paths=(str(outcome), str(code)),
        task_key_recorded=True,
        git_status_paths=(str(outcome), str(code)),
    ))

    assert result.ok


def test_p1_fails_when_outcome_was_not_written(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    code = approved / "src" / "app.py"
    outcome = approved / "outcome.md"

    result = evaluate_worktree_boundary(WorktreeProbeInput(
        approved_cwd=str(approved),
        process_cwd=str(approved),
        outcome_path=str(outcome),
        touched_paths=(str(code),),
        off_limits_paths=(),
        expected_paths=(str(outcome), str(code)),
        task_key_recorded=True,
    ))

    assert not result.ok
    assert result.reason == "outcome_not_written"


def test_p1_fails_when_git_status_contains_unexpected_path(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    outcome = approved / "outcome.md"
    code = approved / "src" / "app.py"
    surprise = approved / "scratch.txt"

    result = evaluate_worktree_boundary(WorktreeProbeInput(
        approved_cwd=str(approved),
        process_cwd=str(approved),
        outcome_path=str(outcome),
        touched_paths=(str(outcome), str(code)),
        off_limits_paths=(),
        expected_paths=(str(outcome), str(code)),
        task_key_recorded=True,
        git_status_paths=(str(outcome), str(code), str(surprise)),
    ))

    assert not result.ok
    assert result.reason == "unexpected_git_status"


def test_p1_fails_when_claude_touches_sibling_worktree(tmp_path):
    approved = tmp_path / "approved"
    other = tmp_path / "other"
    approved.mkdir()
    other.mkdir()
    sentinel = other / "sentinel.txt"

    result = evaluate_worktree_boundary(WorktreeProbeInput(
        approved_cwd=str(approved),
        process_cwd=str(approved),
        outcome_path=str(approved / "outcome.md"),
        touched_paths=(str(sentinel),),
        off_limits_paths=(str(sentinel),),
        expected_paths=(str(approved / "outcome.md"),),
        task_key_recorded=True,
    ))

    assert not result.ok
    assert result.reason in {"outcome_not_written", "touched_outside_worktree", "off_limits_touched"}


def test_p2_worker_invocation_requires_complete_high_volume_capture():
    output = (
        "Specialist: Planner\nDecision: keep scope narrow\n"
        "Specialist: Tester\nDecision: add public-boundary RED\n"
        "Objection: missing redaction fixture\n"
        + ("x" * 5_200)
    )
    result = evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=output,
        captured_bytes=len(output.encode()),
        expected_bytes=len(output.encode()),
        specialist_names=("Planner", "Tester"),
        decisions=("keep scope narrow", "add public-boundary RED"),
        objections=("missing redaction fixture",),
        spawned_by_codex=True,
        orchestration_surface="claude_agents",
        captured_token_estimate=5_200,
        expected_token_estimate=5_000,
    ))

    assert result.ok


def test_p2_fails_on_truncated_capture_even_if_worker_exits_zero():
    output = "Specialist: Planner\nDecision: keep scope narrow\n" + ("x" * 5_200)
    result = evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=output,
        captured_bytes=100,
        expected_bytes=len(output.encode()),
        specialist_names=("Planner",),
        decisions=("keep scope narrow",),
        objections=(),
        spawned_by_codex=True,
        orchestration_surface="claude_agents",
        captured_token_estimate=5_200,
        expected_token_estimate=5_000,
    ))

    assert not result.ok
    assert result.reason == "output_truncated"


def test_p2_fails_when_output_was_not_codex_spawned_or_no_surface_is_recorded():
    output = "Specialist: Planner\nDecision: keep scope narrow\n" + ("x" * 5_200)

    not_spawned = evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=output,
        captured_bytes=len(output.encode()),
        expected_bytes=len(output.encode()),
        specialist_names=("Planner",),
        decisions=("keep scope narrow",),
        objections=(),
        orchestration_surface="claude_agents",
        captured_token_estimate=5_200,
    ))
    assert not not_spawned.ok
    assert not_spawned.reason == "not_codex_spawned"

    no_surface = evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=output,
        captured_bytes=len(output.encode()),
        expected_bytes=len(output.encode()),
        specialist_names=("Planner",),
        decisions=("keep scope narrow",),
        objections=(),
        spawned_by_codex=True,
        captured_token_estimate=5_200,
    ))
    assert not no_surface.ok
    assert no_surface.reason == "missing_orchestration_surface"


def test_p2_fails_when_load_case_is_not_high_volume_tokens():
    output = "Specialist: Planner\nDecision: keep scope narrow\n" + ("x" * 20_000)

    result = evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=output,
        captured_bytes=len(output.encode()),
        expected_bytes=len(output.encode()),
        specialist_names=("Planner",),
        decisions=("keep scope narrow",),
        objections=(),
        spawned_by_codex=True,
        orchestration_surface="claude_agents",
        captured_token_estimate=1_000,
        expected_token_estimate=5_000,
    ))

    assert not result.ok
    assert result.reason == "load_case_too_small"


def test_p3_worker_outcome_adapter_preserves_specialists_decisions_and_objections():
    payload = {
        "task_id": "slice0",
        "summary": "Implemented probe harness.",
        "specialists": [
            {"name": "Planner", "decision": "keep scope narrow", "objection": None},
            {"name": "Tester", "decision": "add public-boundary RED", "objection": "missing redaction fixture"},
            {"name": "Reviewer", "decision": "pause on deadlock", "objection": "budget unclear"},
        ],
        "decisions": ["keep scope narrow", "add public-boundary RED", "pause on deadlock"],
        "objections": ["missing redaction fixture", "budget unclear"],
        "changed_files": ["supervisor/dual_agent.py", "tests/test_dual_agent_slice0.py"],
        "tests": ["pytest tests/test_dual_agent_slice0.py"],
        "test_status": "passed",
        "confidence": 0.91,
    }
    transcript = (
        "worker prose before\n"
        f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>\n"
        "worker prose after\n"
    )

    result, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Planner", "Tester", "Reviewer"),
        expected_decisions=("keep scope narrow", "add public-boundary RED", "pause on deadlock"),
        expected_objections=("missing redaction fixture", "budget unclear"),
    )

    assert result.ok
    assert outcome is not None
    assert outcome.changed_files == ["supervisor/dual_agent.py", "tests/test_dual_agent_slice0.py"]


def test_p3_worker_outcome_adapter_normalizes_live_test_status_aliases():
    payload = {
        "task_id": "slice0",
        "summary": "Live SDK reviewer accepted.",
        "specialists": [{"name": "Cursor Reviewer", "decision": "accept"}],
        "decisions": ["accept"],
        "objections": [],
        "changed_files": [],
        "tests": [],
        "test_status": "pass",
        "confidence": 0.91,
    }
    transcript = f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"

    result, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Cursor Reviewer",),
        expected_decisions=("accept",),
        expected_objections=(),
    )

    assert result.ok
    assert outcome is not None
    assert outcome.test_status == "passed"


def test_p4_outcome_adapter_coerces_object_shaped_decisions_and_preserves_semantics():
    # Regression: leads/reviewers sometimes emit `decisions`/`objections` as
    # objects (the richer dynamic_workflow shape) instead of list[str]. Before
    # the shape-coercion validator this raised "invalid dual_agent_outcome
    # block" and blocked the gate (e.g. prd_review). It must now parse, and P4
    # must still read the accept/block token. (fix 2026-06-06.)
    accept_payload = {
        "task_id": "slice0",
        "summary": "Reviewer accepted PRD.",
        "decisions": [
            {"gate": "prd_review", "decision": "accept", "rationale": "scope ok"},
        ],
        "objections": [{"objection": "none"}],
        "changed_files": [],
        "tests": [],
        "test_status": "unknown",
        "confidence": 0.8,
    }
    transcript = f"<dual_agent_outcome>{json.dumps(accept_payload)}</dual_agent_outcome>"
    result, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=(),
        expected_decisions=("accept",),
        expected_objections=(),
    )
    assert result.ok  # parsed instead of "invalid dual_agent_outcome block"
    assert outcome is not None
    assert all(isinstance(d, str) for d in outcome.decisions)
    assert "accept" in outcome.decisions
    assert evaluate_outcome_gate_decision(outcome).ok  # P4 green

    # A revise object must STILL block (semantics preserved, not weakened).
    revise_payload = {
        **accept_payload,
        "summary": "Reviewer asked for revision.",
        "decisions": [{"decision": "revise", "rationale": "tighten scope"}],
    }
    _, revise_outcome = evaluate_outcome_fidelity(
        f"<dual_agent_outcome>{json.dumps(revise_payload)}</dual_agent_outcome>",
        expected_specialists=(),
        expected_decisions=("revise",),
        expected_objections=(),
    )
    assert revise_outcome is not None
    revise_probe = evaluate_outcome_gate_decision(revise_outcome)
    assert not revise_probe.ok
    assert revise_probe.reason == "outcome_critical_review_blocked"


def test_p3_fails_when_adapter_drops_worker_signal():
    payload = {
        "task_id": "slice0",
        "summary": "Too thin.",
        "specialists": [{"name": "Planner", "decision": "keep scope narrow"}],
        "changed_files": ["supervisor/dual_agent.py"],
        "tests": ["pytest tests/test_dual_agent_slice0.py"],
        "test_status": "passed",
        "confidence": 0.91,
    }
    transcript = f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"

    result, _ = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Planner", "Tester"),
        expected_decisions=("keep scope narrow", "add public-boundary RED"),
        expected_objections=("missing redaction fixture",),
    )

    assert not result.ok
    assert result.reason == "outcome_signal_loss"
    assert result.details["specialists"] == ["Tester"]


def test_p3_fails_when_required_review_evidence_is_not_explicit():
    payload = {
        "task_id": "slice0",
        "summary": "Too thin.",
        "specialists": [{"name": "Planner", "decision": "keep scope narrow"}],
    }
    transcript = f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"

    result, _ = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Planner",),
        expected_decisions=("keep scope narrow",),
        expected_objections=(),
    )

    assert not result.ok
    assert result.reason == "outcome_missing_required_fields"
    assert result.details["fields"] == ["changed_files", "tests", "test_status", "confidence"]


def test_p4_deadlock_budget_records_pause_not_auto_decision(tmp_path):
    state = State(str(tmp_path / "state.db"))
    rounds = [
        GateRound(
            round_index=i,
            codex_decision="deny",
            claude_decision="accept",
            codex_confidence=0.96,
            claude_confidence=0.95,
            objection="Codex sees missing tests; Claude thinks tests are unnecessary.",
        )
        for i in range(1, 4)
    ]

    result = evaluate_deadlock_budget(
        rounds,
        per_gate_cap=3,
        task_budget=8,
        state=state,
        run_id="run-dual",
    )

    assert result.ok
    assert result.reason == "paused_for_human"
    row = state._conn.execute("SELECT * FROM actions").fetchone()
    assert row["action_type"] == "dual_agent_gate_deadlock"
    assert row["status"] == "paused_for_human"
    payload = json.loads(row["payload_json"])
    assert payload["escalation_type"] == "kill_or_pause"


def test_p4_deadlock_budget_does_not_pause_when_final_round_accepts(tmp_path):
    state = State(str(tmp_path / "state.db"))
    rounds = [
        GateRound(
            round_index=1,
            codex_decision="deny",
            claude_decision="accept",
            codex_confidence=0.96,
            claude_confidence=0.95,
            objection="Codex sees missing tests; Claude thinks tests are unnecessary.",
        ),
        GateRound(
            round_index=2,
            codex_decision="revise",
            claude_decision="revise",
            codex_confidence=0.85,
            claude_confidence=0.84,
            objection="Both agents want a narrower test plan.",
        ),
        GateRound(
            round_index=3,
            codex_decision="accept",
            claude_decision="accept",
            codex_confidence=0.96,
            claude_confidence=0.95,
            objection="Resolved.",
        ),
    ]

    result = evaluate_deadlock_budget(
        rounds,
        per_gate_cap=3,
        task_budget=8,
        state=state,
        run_id="run-dual",
    )

    assert result.ok
    assert result.reason == "not_deadlocked"
    assert state._conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


def test_p5_artifact_redaction_covers_markdown_and_gate_logs():
    result = evaluate_artifact_redaction({
        "outcome.md": "Implemented. API_KEY=secret-value",
        "gate_log.jsonl": '{"auth":"Authorization: Bearer secret-token"}',
        "handover.md": "sk-ant-secretvalue",
    })

    assert result.ok
    blob = json.dumps(result.details["artifacts"])
    assert "secret-value" not in blob
    assert "secret-token" not in blob
    assert "sk-ant-secretvalue" not in blob
    assert "[REDACTED" in blob


def test_p6_test_coverage_gate_asks_one_bounded_followup_for_code_without_tests():
    result = evaluate_test_coverage_gate(["supervisor/dual_agent.py"])

    assert result == {
        "status": "needs_followup",
        "question": BOUNDED_TEST_FOLLOWUP,
        "max_tokens": 200,
        "rounds": 1,
        "counts_against_budget": False,
        "auto_deny": False,
    }


def test_p6_test_coverage_gate_passes_when_test_file_changed():
    assert evaluate_test_coverage_gate([
        "supervisor/dual_agent.py",
        "tests/test_dual_agent_slice0.py",
    ])["status"] == "ok"


def test_p6_test_coverage_gate_recognizes_common_test_filename_patterns():
    assert evaluate_test_coverage_gate([
        "src/widget.ts",
        "src/widget.test.ts",
    ])["status"] == "ok"
    assert evaluate_test_coverage_gate([
        "src/service.py",
        "src/service_test.py",
    ])["status"] == "ok"
    assert evaluate_test_coverage_gate([
        "src/view.tsx",
        "src/view.spec.tsx",
    ])["status"] == "ok"


def test_p7_telegram_batches_fyis_but_sends_alerts_approvals_and_milestones():
    events = [
        GateEvent("task-a", "fyi", "proposal v1", 0),
        GateEvent("task-a", "fyi", "critique v1", 10),
        GateEvent("task-a", "alert", "deadlock", 15),
        GateEvent("task-a", "approval", "approve relaxation?", 16),
        GateEvent("task-a", "milestone", "shipped", 17),
    ]

    notifications = plan_telegram_notifications(events, cooldown_s=60)

    assert [n["kind"] for n in notifications] == [
        "fyi_batch",
        "alert",
        "approval",
        "milestone",
    ]
    assert notifications[0]["events"] == ["proposal v1", "critique v1"]


def test_p10_parallel_isolation_requires_task_prefix_and_unique_worktree(tmp_path):
    result = validate_parallel_isolation([
        {
            "task_id": "task-a",
            "telegram_text": "[task-a] milestone shipped",
            "worktree": str(tmp_path / "a"),
        },
        {
            "task_id": "task-b",
            "telegram_text": "[task-b] milestone shipped",
            "worktree": str(tmp_path / "b"),
        },
    ])

    assert result.ok


def test_p10_fails_on_shared_worktree_or_ambiguous_telegram():
    result = validate_parallel_isolation([
        {"task_id": "task-a", "telegram_text": "milestone shipped", "worktree": "/tmp/shared"},
        {"task_id": "task-b", "telegram_text": "[task-b] milestone shipped", "worktree": "/tmp/shared"},
    ])

    assert not result.ok
    assert result.reason == "telegram_missing_task_prefix"


def test_p11_claim_verification_flags_unverified_agent_claims():
    outcome = Outcome(
        task_id="slice0",
        summary="Done",
        claims=["cron armed", "deployed to staging"],
    )

    result = verify_outcome_claims(outcome, evidence={"verified_claims": ["cron armed"]})

    assert not result.ok
    assert result.reason == "unverified_claims"
    assert result.details["claims"] == ["deployed to staging"]


def test_hard_stop_summary_blocks_until_all_required_probes_are_green():
    results = [
        evaluate_credential_boundary(CredentialProbeInput(
            explicit_env={"ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com"},
            ambient_env={},
            reported_request_host="uai-litellm.internal.unity.com",
            intended_gateway_host="uai-litellm.internal.unity.com",
            public_artifacts={},
        )),
    ]

    summary = slice0_hard_stop_summary(results)

    assert summary["status"] == "blocked"
    assert summary["missing"] == ["P1", "P2", "P3", "P4"]
    assert summary["desktop_visibility"] == "history_only"


def test_hard_stop_summary_requires_p4_pause_evidence_not_just_green_p4(tmp_path):
    state = State(str(tmp_path / "state.db"))
    not_deadlocked_p4 = evaluate_deadlock_budget(
        [
            GateRound(
                round_index=1,
                codex_decision="accept",
                claude_decision="accept",
                codex_confidence=0.96,
                claude_confidence=0.96,
                objection="Resolved.",
            )
        ],
        per_gate_cap=3,
        task_budget=8,
        state=state,
    )

    summary = slice0_hard_stop_summary([
        ProbeResult("P0", "green", "credential_boundary_ok"),
        ProbeResult("P1", "green", "worktree_boundary_ok"),
        ProbeResult("P2", "green", "worker_orchestration_invocation_ok"),
        ProbeResult("P3", "green", "outcome_fidelity_ok"),
        not_deadlocked_p4,
    ])

    assert not_deadlocked_p4.ok
    assert not_deadlocked_p4.reason == "not_deadlocked"
    assert summary["status"] == "blocked"
    assert summary["red"] == ["P4"]
