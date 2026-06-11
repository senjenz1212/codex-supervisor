from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from supervisor.runtime_evidence import (
    _prepare_validation_copy,
    _run_declared_tests,
    _test_commands,
    collect_runtime_evidence,
)


def _write_test(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_bare_pytest_test_name_resolves_to_unique_nodeid(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_runtime_target.py",
        "def test_runtime_target_value():\n"
        "    assert True\n",
    )

    assert _test_commands(["test_runtime_target_value"], tmp_path) == [
        f"{shlex.quote(sys.executable)} -m pytest "
        "tests/test_runtime_target.py::test_runtime_target_value -q"
    ]


def test_python_pytest_command_resolves_bare_test_name(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_runtime_target.py",
        "class TestRuntimeEvidence:\n"
        "    def test_runtime_target_value(self):\n"
        "        assert True\n",
    )

    assert _test_commands(
        ["python -m pytest TestRuntimeEvidence::test_runtime_target_value -q"],
        tmp_path,
    ) == [
        f"{shlex.quote(sys.executable)} -m pytest "
        "tests/test_runtime_target.py::TestRuntimeEvidence::test_runtime_target_value -q"
    ]


def test_ambiguous_bare_pytest_name_stays_unresolved(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_one.py",
        "def test_shared_name():\n"
        "    assert True\n",
    )
    _write_test(
        tmp_path / "tests" / "test_two.py",
        "def test_shared_name():\n"
        "    assert True\n",
    )

    assert _test_commands(["test_shared_name"], tmp_path) == [
        f"{shlex.quote(sys.executable)} -m pytest test_shared_name -q"
    ]


def test_pytest_label_with_file_line_and_test_name_resolves_to_nodeid(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_autoresearch.py",
        "def test_autoresearch_live_evaluator_detects_absolute_source_checkout_mutation():\n"
        "    assert True\n",
    )

    assert _test_commands(
        [
            "test_autoresearch.py:499 "
            "test_autoresearch_live_evaluator_detects_absolute_source_checkout_mutation "
            "(corrective P3; absolute repo_root write)"
        ],
        tmp_path,
    ) == [
        f"{shlex.quote(sys.executable)} -m pytest "
        "tests/test_autoresearch.py::"
        "test_autoresearch_live_evaluator_detects_absolute_source_checkout_mutation -q"
    ]


def test_pytest_label_with_only_file_resolves_to_test_file(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_autoresearch.py",
        "def test_one():\n"
        "    assert True\n",
    )

    assert _test_commands(
        ["tests/test_autoresearch.py (report-only invariants all False)"],
        tmp_path,
    ) == [
        f"{shlex.quote(sys.executable)} -m pytest tests/test_autoresearch.py -q"
    ]


def test_declared_make_test_stays_allowlisted_argv_command(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    assert _test_commands(["make test"], tmp_path) == ["make test"]

    def fake_runner(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["shell"] = kwargs["shell"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=_test_commands(["make test"], tmp_path),
        timeout_s=30,
        runner=fake_runner,
    )

    assert receipt is not None
    assert receipt["status"] == "passed"
    assert captured["argv"] == ["make", "test"]
    assert captured["shell"] is False


def test_validation_copy_ignores_cortex_runtime_workspaces(tmp_path: Path) -> None:
    _write_test(tmp_path / "src" / "app.py", "VALUE = 1\n")
    _write_test(tmp_path / ".cortex" / "settings.json", "{}\n")
    _write_test(
        tmp_path / ".cortex" / "runtime_workspaces" / "run-1" / ".codex" / "tmp" / "apply_patch",
        "transient\n",
    )

    workspace = _prepare_validation_copy(tmp_path)
    validation_cwd = Path(workspace["validation_cwd"])

    try:
        assert (validation_cwd / "src" / "app.py").exists()
        assert (validation_cwd / ".cortex" / "settings.json").exists()
        assert not (validation_cwd / ".cortex" / "runtime_workspaces").exists()
    finally:
        import shutil

        shutil.rmtree(workspace["temp_parent"], ignore_errors=True)


def test_runtime_evidence_derives_changed_files_from_committed_diff(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    _write_test(tmp_path / "src" / "app.py", "VALUE = 1\n")
    subprocess.run(["git", "add", "src/app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True, text=True)
    baseline_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    _write_test(tmp_path / "src" / "app.py", "VALUE = 2\n")
    subprocess.run(["git", "add", "src/app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "implementation"], cwd=tmp_path, check=True, capture_output=True, text=True)
    outcome: dict[str, object] = {
        "changed_files": [],
        "claims": ["implemented"],
        "decisions": ["accept"],
        "summary": "Implemented and committed.",
    }

    result = collect_runtime_evidence(
        cwd=tmp_path,
        task_id="committed-work",
        run_id="run-1",
        gate="execution",
        round_index=1,
        baseline={"status": "passed", "head": baseline_head, "reason": "git_head_captured"},
        outcome_payload=outcome,
        runner=subprocess.run,
    )

    assert result.probe.ok
    assert outcome["changed_files"] == ["src/app.py"]
    diff_receipt = next(
        receipt for receipt in result.receipts
        if receipt["receipt_id"] == "runtime-git-diff-execution-1"
    )
    assert diff_receipt["committed_changed_files"] == ["src/app.py"]
    assert diff_receipt["derived_changed_files_from_runtime"] is True


def test_declared_vela_eval_runner_command_is_allowlisted(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_runner(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["shell"] = kwargs["shell"]
        return subprocess.CompletedProcess(argv, 0, stdout="suite ok\n", stderr="")

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=["python -m cortex.vela_eval.runner tests/fixtures/vela_eval/smoke"],
        timeout_s=30,
        runner=fake_runner,
    )

    assert receipt is not None
    assert receipt["status"] == "passed"
    assert captured["argv"][1:] == [
        "-m",
        "cortex.vela_eval.runner",
        "tests/fixtures/vela_eval/smoke",
    ]
    assert captured["shell"] is False


def test_declared_vela_surface_truth_make_target_is_allowlisted(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_runner(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["shell"] = kwargs["shell"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=["make smoke-vela2-surface-truth"],
        timeout_s=30,
        runner=fake_runner,
    )

    assert receipt is not None
    assert receipt["status"] == "passed"
    assert captured["argv"] == ["make", "smoke-vela2-surface-truth"]
    assert captured["shell"] is False


def test_declared_python_c_command_is_rejected_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=[f'{sys.executable} -c "from pathlib import Path; Path({str(marker)!r}).write_text(\\\"x\\\")"'],
        timeout_s=30,
        runner=subprocess.run,
    )

    assert receipt is not None
    result = receipt["results"][0]
    assert receipt["status"] == "failed"
    assert result["reason"] == "runtime_test_command_rejected"
    assert result["argv"] == []
    assert not marker.exists()


def test_allowlisted_pytest_command_runs_and_reports_pass_fail(tmp_path: Path) -> None:
    _write_test(
        tmp_path / "tests" / "test_runtime_ok.py",
        "def test_runtime_ok():\n"
        "    assert True\n",
    )
    _write_test(
        tmp_path / "tests" / "test_runtime_fail.py",
        "def test_runtime_fail():\n"
        "    assert False\n",
    )

    passed = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=["python -m pytest tests/test_runtime_ok.py -q"],
        timeout_s=30,
        runner=subprocess.run,
    )
    failed = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=2,
        test_commands=["python -m pytest tests/test_runtime_fail.py -q"],
        timeout_s=30,
        runner=subprocess.run,
    )

    assert passed is not None
    assert passed["status"] == "passed"
    assert passed["results"][0]["argv"][1:3] == ["-m", "pytest"]
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["results"][0]["returncode"] != 0


def test_runtime_test_subprocess_env_drops_secret_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("SECRET_TOKEN", "secret")
    captured_env: dict[str, str] = {}

    def fake_runner(argv, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=["python -m pytest tests/test_runtime_ok.py -q"],
        timeout_s=30,
        runner=fake_runner,
    )

    assert receipt is not None
    assert receipt["status"] == "passed"
    assert "OPENAI_API_KEY" not in captured_env
    assert "ANTHROPIC_API_KEY" not in captured_env
    assert "SECRET_TOKEN" not in captured_env
    assert captured_env["PYTHONNOUSERSITE"] == "1"


def test_runtime_test_environment_unavailable_is_red(tmp_path: Path) -> None:
    def missing_pytest_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No module named pytest\n")

    receipt = _run_declared_tests(
        tmp_path,
        gate="execution",
        round_index=1,
        test_commands=["python -m pytest tests/test_runtime_ok.py -q"],
        timeout_s=30,
        runner=missing_pytest_runner,
    )

    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["results"][0]["reason"] == "runtime_test_environment_unavailable"
