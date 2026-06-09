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


def test_no_mistakes_clean_branch_runs_in_isolated_worktree(tmp_path):
    from supervisor.no_mistakes import (
        NoMistakesConfig,
        NoMistakesValidationRequest,
        run_no_mistakes_validation,
    )

    _init_git_repo(tmp_path)
    runner_cwds: list[str] = []

    def fake_runner(argv, **kwargs):
        runner_cwds.append(str(kwargs["cwd"]))
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

    assert result.isolated_worktree is True
    assert runner_cwds
    assert Path(runner_cwds[0]) != tmp_path
    assert "isolated-change.txt" in result.changed_files_after
    assert not (tmp_path / "isolated-change.txt").exists()


def test_no_mistakes_runner_exception_cleans_isolated_worktree(tmp_path):
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
    assert result.isolated_worktree is True
    assert result.validation_cwd is not None
    assert not Path(result.validation_cwd).exists()
    assert not (tmp_path / "partial-output.txt").exists()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)
