from __future__ import annotations

import shlex
import sys
from pathlib import Path

from supervisor.runtime_evidence import _test_commands


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
