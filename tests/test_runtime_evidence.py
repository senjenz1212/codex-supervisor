from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from supervisor.runtime_evidence import _run_declared_tests, _test_commands


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
