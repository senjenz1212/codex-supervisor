from __future__ import annotations

import subprocess
from pathlib import Path


def test_no_mistakes_config_defaults_are_safe(tmp_path):
    from supervisor.config import Config

    cfg = Config(**{
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

    assert cfg.no_mistakes.policy == "off"
    assert cfg.no_mistakes.skip_steps == ["push", "pr", "ci"]
    assert cfg.no_mistakes.auto_yes is False
    assert cfg.no_mistakes.allow_shipping_steps is False


def test_no_mistakes_adapter_builds_safe_default_command(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate the accepted supervisor outcome.",
            config=NoMistakesConfig(policy="advisory", require_clean_committed_branch=False),
        ),
        runner=fake_runner,
    )

    assert result.verdict == "accepted"
    assert calls == [[
        "no-mistakes",
        "axi",
        "run",
        "--intent",
        "Validate the accepted supervisor outcome.",
        "--skip=push,pr,ci",
    ]]
    assert "--yes" not in calls[0]


def test_no_mistakes_auto_yes_requires_shipping_opt_in(tmp_path):
    from supervisor.no_mistakes import NoMistakesConfig, build_no_mistakes_command

    advisory = build_no_mistakes_command(
        NoMistakesConfig(
            policy="advisory",
            auto_yes=True,
            require_clean_committed_branch=False,
        ),
        intent="Validate safely.",
    )
    shipping = build_no_mistakes_command(
        NoMistakesConfig(
            policy="shipping",
            auto_yes=True,
            allow_shipping_steps=True,
            skip_steps=(),
        ),
        intent="Validate shipping path.",
    )

    assert "--yes" not in advisory
    assert "--yes" in shipping
    assert not any(arg.startswith("--skip=") for arg in shipping)


def test_no_mistakes_missing_binary_policy_matrix(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def missing_runner(argv, **kwargs):
        raise FileNotFoundError("no-mistakes")

    advisory = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory", require_clean_committed_branch=False),
        ),
        runner=missing_runner,
    )
    required = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=missing_runner,
    )

    assert advisory.verdict == "unavailable"
    assert advisory.status == "skipped"
    assert required.verdict == "required_blocked"
    assert required.status == "blocked"


def test_no_mistakes_adapter_parses_outcome_and_gate_findings(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def accepted_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    accepted = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory", require_clean_committed_branch=False),
        ),
        runner=accepted_runner,
    )
    assert accepted.verdict == "accepted"
    assert accepted.findings == ()

    def finding_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "gate: review\n"
                "findings:\n"
                "  r1,error,supervisor/no_mistakes.py,Missing timeout,auto-fix\n"
            ),
            stderr="",
        )

    blocked = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=finding_runner,
    )

    assert blocked.verdict == "required_blocked"
    assert blocked.status == "blocked"
    assert blocked.findings[0].finding_id == "r1"
    assert blocked.findings[0].action == "auto-fix"


def test_no_mistakes_adapter_blocks_toon_review_gate_with_action_column_before_description(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def toon_gate_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "step: review\n"
                "status: awaiting_approval\n"
                "findings[3]{id,severity,file,action,description}:\n"
                "  string-sort-attempt-dirs,info,cortex/vela_eval/production_confidence.py,no-op,Latent sort issue for attempt-10 vs attempt-2\n"
                "  auth-secret-reused-as-hmac-key,warning,cortex/task_store/agents_substrate.py,ask-user,Fallback HMAC signing key uses CORTEX_GATEWAY_SERVICE_AUTH, also bearer token\n"
                "  supervisor-swallows-unexpected-exceptions,info,cortex/vela_supervisor/service.py,no-op,Unexpected exceptions are reported as degraded observations\n"
            ),
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-toon-gate",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=toon_gate_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.status == "blocked"
    assert result.reason == "no_mistakes_gate_review"
    assert [finding.finding_id for finding in result.findings] == [
        "string-sort-attempt-dirs",
        "auth-secret-reused-as-hmac-key",
        "supervisor-swallows-unexpected-exceptions",
    ]
    ask_user = result.findings[1]
    assert ask_user.action == "ask-user"
    assert ask_user.description == (
        "Fallback HMAC signing key uses CORTEX_GATEWAY_SERVICE_AUTH, also bearer token"
    )
    assert result.blocking_findings[0].finding_id == "auth-secret-reused-as-hmac-key"


def test_no_mistakes_required_blocks_exit_zero_without_outcome_or_findings(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def incomplete_toon_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="status: complete\n",
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-missing-outcome",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=incomplete_toon_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.status == "blocked"
    assert result.reason == "no_mistakes_missing_outcome"
    assert result.findings == ()


def test_no_mistakes_adapter_parses_structured_json_contract(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def structured_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"schema_version":"no-mistakes-report/v1",'
                '"outcome":"blocked",'
                '"gate":"review",'
                '"findings":[{"id":"json-1","severity":"error",'
                '"path":"supervisor/no_mistakes.py","line":12,'
                '"message":"structured finding","action":"ask-user"}]}\n'
            ),
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-structured",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=structured_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.reason == "no_mistakes_gate_review"
    assert result.findings[0].finding_id == "json-1"
    assert result.findings[0].file == "supervisor/no_mistakes.py"
    assert result.findings[0].line == 12


def test_no_mistakes_structured_checks_passed_with_gate_accepts(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def structured_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"schema_version":"no-mistakes-report/v1",'
                '"outcome":"checks_passed",'
                '"gate":"review",'
                '"findings":[]}\n'
            ),
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-structured-pass",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=structured_runner,
    )

    assert result.verdict == "accepted"
    assert result.status == "accepted"
    assert result.reason == "no_mistakes_checks_passed"
    assert result.findings == ()


def test_no_mistakes_required_blocks_malformed_structured_json(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def malformed_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"schema_version":"no-mistakes-report/v1","findings":[}\n',
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-malformed",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=malformed_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.status == "blocked"
    assert result.reason == "no_mistakes_gate_no_mistakes_structured_contract"
    assert result.findings[0].finding_id == "no-mistakes-malformed-json"


def test_no_mistakes_advisory_records_malformed_structured_json_as_secondary_evidence(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def malformed_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"schema_version":"no-mistakes-report/v1","outcome":\n',
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-advisory-malformed",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory", require_clean_committed_branch=False),
        ),
        runner=malformed_runner,
    )

    assert result.verdict == "advisory_blocked"
    assert result.reason == "no_mistakes_gate_no_mistakes_structured_contract"
    assert result.to_receipt()["kind"] == "no_mistakes_validation_receipt"
    assert result.findings[0].severity == "error"


def test_no_mistakes_required_blocks_incomplete_structured_json_contract(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def incomplete_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"schema_version":"no-mistakes-report/v1"}\n',
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-incomplete",
            intent="Validate.",
            config=NoMistakesConfig(policy="required", require_clean_committed_branch=False),
        ),
        runner=incomplete_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.status == "blocked"
    assert result.reason == "no_mistakes_gate_no_mistakes_structured_contract"
    assert result.findings[0].finding_id == "no-mistakes-incomplete-contract"


def test_no_mistakes_advisory_records_incomplete_structured_json_contract(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    def incomplete_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"schema_version":"no-mistakes-report/v1","findings":[]}\n',
            stderr="",
        )

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-advisory-incomplete",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory", require_clean_committed_branch=False),
        ),
        runner=incomplete_runner,
    )

    assert result.verdict == "advisory_blocked"
    assert result.reason == "no_mistakes_gate_no_mistakes_structured_contract"
    assert result.findings[0].finding_id == "no-mistakes-incomplete-contract"
    assert result.to_receipt()["status"] == "blocked"


def test_no_mistakes_changes_require_supervisor_rerun(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)

    def changing_runner(argv, **kwargs):
        (Path(kwargs["cwd"]) / "changed.py").write_text("print('changed')\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory"),
        ),
        runner=changing_runner,
    )

    assert result.verdict == "changed_requires_rerun"
    assert "changed.py" in result.changed_files_after
    assert result.to_receipt()["status"] == "blocked"


def test_no_mistakes_artifact_stash_restore(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)
    artifact = tmp_path / "docs" / "dual-agent" / "task-1" / "index.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", str(artifact.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add workflow artifact"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact.write_text("after\n", encoding="utf-8")

    runner_statuses: list[str] = []

    def clean_runner(argv, **kwargs):
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=kwargs["cwd"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        runner_statuses.append(status)
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-artifacts",
            intent="Validate.",
            config=NoMistakesConfig(policy="required"),
        ),
        runner=clean_runner,
    )

    assert result.verdict == "accepted"
    assert runner_statuses == [""]
    assert artifact.read_text(encoding="utf-8") == "after\n"
    assert "docs/dual-agent/task-1/index.md" in subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "codex-supervisor:no-mistakes-artifacts" not in subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_no_mistakes_validator_writes_to_stashed_prefix_do_not_force_rerun(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)
    artifact = tmp_path / "docs" / "dual-agent" / "task-1" / "index.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", str(artifact.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add workflow artifact"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact.write_text("after\n", encoding="utf-8")

    def validator_writes_to_artifact_prefix(argv, **kwargs):
        validator_output = Path(kwargs["cwd"]) / "docs" / "dual-agent" / "task-1" / "verdict.json"
        validator_output.parent.mkdir(parents=True, exist_ok=True)
        validator_output.write_text("{\"ok\": true}\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-artifact-write",
            intent="Validate.",
            config=NoMistakesConfig(policy="required"),
        ),
        runner=validator_writes_to_artifact_prefix,
    )

    assert result.verdict == "accepted"
    assert result.reason == "no_mistakes_checks_passed"


def test_no_mistakes_stash_apply_conflict_rolls_back_and_drops_stash(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)
    artifact = tmp_path / "docs" / "dual-agent" / "task-1" / "index.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", str(artifact.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add workflow artifact"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    artifact.write_text("after\n", encoding="utf-8")

    def validator_overwrites_stashed_artifact_with_conflict(argv, **kwargs):
        artifact_in_cwd = Path(kwargs["cwd"]) / "docs" / "dual-agent" / "task-1" / "index.md"
        artifact_in_cwd.parent.mkdir(parents=True, exist_ok=True)
        artifact_in_cwd.write_text("conflicting-validator-write\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", str(artifact_in_cwd.relative_to(kwargs["cwd"]))],
            cwd=kwargs["cwd"],
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "conflicting validator write"],
            cwd=kwargs["cwd"],
            check=True,
            capture_output=True,
            text=True,
        )
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-stash-conflict",
            intent="Validate.",
            config=NoMistakesConfig(policy="required"),
        ),
        runner=validator_overwrites_stashed_artifact_with_conflict,
    )

    assert result.verdict in {"required_blocked", "advisory_blocked"}
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "codex-supervisor:no-mistakes-artifacts" not in stash_list


def test_no_mistakes_clean_branch_runs_on_checked_out_branch(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)
    runner_cwds: list[str] = []
    runner_branches: list[str] = []

    def fake_runner(argv, **kwargs):
        runner_cwds.append(str(kwargs["cwd"]))
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=kwargs["cwd"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        runner_branches.append(branch)
        (Path(kwargs["cwd"]) / "isolated-change.txt").write_text("changed\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="advisory"),
        ),
        runner=fake_runner,
    )

    assert result.isolated_worktree is False
    assert runner_cwds
    assert Path(runner_cwds[0]) == tmp_path
    assert runner_branches[0] in {"main", "master"}
    assert result.verdict == "changed_requires_rerun"
    assert "isolated-change.txt" in result.changed_files_after
    assert (tmp_path / "isolated-change.txt").exists()


def test_no_mistakes_runner_exception_reports_branch_state_without_cleanup(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)

    def failing_runner(argv, **kwargs):
        (Path(kwargs["cwd"]) / "partial-output.txt").write_text("changed\n", encoding="utf-8")
        raise RuntimeError("validation process crashed")

    result = run_no_mistakes_validation(
        NoMistakesValidationRequest(
            cwd=tmp_path,
            task_id="task-1",
            run_id="run-1",
            intent="Validate.",
            config=NoMistakesConfig(policy="required"),
        ),
        runner=failing_runner,
    )

    assert result.verdict == "required_blocked"
    assert result.status == "failed"
    assert result.reason == "no_mistakes_runner_exception"
    assert result.isolated_worktree is False
    assert result.validation_cwd is not None
    assert Path(result.validation_cwd).exists()
    assert (tmp_path / "partial-output.txt").exists()


def test_rollback_failed_stash_apply_missing_prefix_fails_closed(tmp_path):
    from supervisor.no_mistakes import _rollback_failed_stash_apply

    _init_git_repo(tmp_path)
    untracked = tmp_path / "operator-scratch.txt"
    untracked.write_text("preserve-me\n", encoding="utf-8")
    nested_untracked = tmp_path / "nested" / "operator-notes.md"
    nested_untracked.parent.mkdir()
    nested_untracked.write_text("also-preserve\n", encoding="utf-8")
    pre_stash = subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    stderr = _rollback_failed_stash_apply(tmp_path, "", "stash@{0}")

    assert "no_mistakes_artifact_restore_missing_prefix" in stderr
    assert untracked.exists()
    assert nested_untracked.exists()
    post_stash = subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert pre_stash == post_stash


def test_rollback_failed_stash_apply_scoped_clean_preserves_outside_prefix(tmp_path):
    from supervisor.no_mistakes import _rollback_failed_stash_apply

    _init_git_repo(tmp_path)
    artifact_prefix = tmp_path / "docs" / "dual-agent" / "task-1"
    artifact_prefix.mkdir(parents=True)
    tracked_artifact = artifact_prefix / "tracked.txt"
    tracked_artifact.write_text("baseline\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", str(tracked_artifact.relative_to(tmp_path))],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add artifact"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_artifact.write_text("modified\n", encoding="utf-8")
    subprocess.run(
        ["git", "stash", "push", "-m", "fake-stash"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    inside_prefix_untracked = artifact_prefix / "scratch-conflict.txt"
    inside_prefix_untracked.write_text("inside-prefix\n", encoding="utf-8")
    outside_prefix_untracked = tmp_path / "unrelated-operator-file.txt"
    outside_prefix_untracked.write_text("must-survive\n", encoding="utf-8")

    stderr = _rollback_failed_stash_apply(
        tmp_path, str(artifact_prefix.relative_to(tmp_path)), "stash@{0}"
    )

    assert "fatal" not in stderr.lower()
    assert outside_prefix_untracked.exists()
    assert not inside_prefix_untracked.exists()
    assert tracked_artifact.read_text(encoding="utf-8") == "baseline\n"
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "fake-stash" not in stash_list


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)
