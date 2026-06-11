"""Supervisor-owned runtime evidence for dual-agent workflow gates."""
from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .dual_agent import ProbeResult
from .receipt_provenance import mark_supervisor_runtime_receipt

Runner = Callable[..., subprocess.CompletedProcess[str]]

_SHELL_META_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "2>", "2>>"}
_SECRET_ENV_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|credential|auth|private[_-]?key)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuntimeEvidenceResult:
    probe: ProbeResult
    receipts: tuple[dict[str, Any], ...]
    event_payload: dict[str, Any]


def capture_runtime_baseline(cwd: str | Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    cwd_path = Path(cwd).expanduser()
    started = time.time()
    head = _run_git(cwd_path, ["rev-parse", "HEAD"], runner=runner)
    if head.returncode != 0:
        return {
            "status": "failed",
            "head": None,
            "captured_at": started,
            "reason": "git_head_unavailable",
            "stderr": _tail(head.stderr),
        }
    return {
        "status": "passed",
        "head": head.stdout.strip(),
        "captured_at": started,
        "reason": "git_head_captured",
    }


def collect_runtime_evidence(
    *,
    cwd: str | Path,
    task_id: str,
    run_id: str,
    gate: str,
    round_index: int,
    baseline: dict[str, Any],
    outcome_payload: dict[str, Any],
    test_timeout_s: int = 120,
    runner: Runner = subprocess.run,
) -> RuntimeEvidenceResult:
    cwd_path = Path(cwd).expanduser().resolve()
    changed_files = _text_list(outcome_payload.get("changed_files"))
    test_commands = _test_commands(outcome_payload.get("tests"), cwd_path)
    failures: list[str] = []
    receipts: list[dict[str, Any]] = []

    baseline_head = str(baseline.get("head") or "")
    baseline_ok = bool(baseline_head) and baseline.get("status") == "passed"
    baseline_receipt = _receipt(
        receipt_id=f"runtime-baseline-{gate}-{round_index}",
        kind="runtime_baseline",
        status="passed" if baseline_ok else "failed",
        gate=gate,
        changed_files=[],
        extra={
            "baseline_head": baseline_head or None,
            "reason": baseline.get("reason"),
            "captured_at": baseline.get("captured_at"),
        },
    )
    receipts.append(baseline_receipt)
    if not baseline_ok:
        failures.append("runtime_baseline_unavailable")

    diff_result = _current_changed_files(
        cwd_path,
        baseline_head=baseline_head if baseline_ok else None,
        runner=runner,
    )
    committed_changed_files = diff_result.get("committed_changed_files", [])
    if not changed_files and committed_changed_files:
        changed_files = _text_list(committed_changed_files)
        outcome_payload["changed_files"] = changed_files
    actual_changed_files = diff_result["changed_files"]
    missing_from_diff = sorted(set(changed_files) - set(actual_changed_files))
    if diff_result["status"] != "passed":
        failures.append("runtime_diff_unavailable")
    if missing_from_diff:
        failures.append("runtime_changed_files_missing_from_diff")

    diff_status = "present"
    if diff_result["status"] != "passed" or missing_from_diff:
        diff_status = "failed"
    receipts.append(_receipt(
        receipt_id=f"runtime-git-diff-{gate}-{round_index}",
        kind="git_diff",
        status=diff_status,
        gate=gate,
        changed_files=actual_changed_files,
        claims=["implemented"] if changed_files else [],
        extra={
            "baseline_head": baseline_head or None,
            "declared_changed_files": changed_files,
            "actual_changed_files": actual_changed_files,
            "missing_from_diff": missing_from_diff,
            "extra_actual_files": sorted(set(actual_changed_files) - set(changed_files)),
            "worktree_changed_files": diff_result.get("worktree_changed_files", []),
            "committed_changed_files": committed_changed_files,
            "derived_changed_files_from_runtime": bool(committed_changed_files)
            and _text_list(outcome_payload.get("changed_files")) == committed_changed_files,
            "name_status": diff_result.get("name_status", []),
            "reason": diff_result.get("reason"),
        },
    ))

    deliverable_checks = _deliverable_checks(cwd_path, changed_files)
    deliverable_failures = [
        item["reason"]
        for item in deliverable_checks
        if item["status"] != "passed"
    ]
    failures.extend(deliverable_failures)
    receipts.append(_receipt(
        receipt_id=f"runtime-deliverables-{gate}-{round_index}",
        kind="runtime_deliverable_check",
        status="passed" if not deliverable_failures else "failed",
        gate=gate,
        changed_files=changed_files,
        extra={"checks": deliverable_checks},
    ))

    test_receipt = _run_declared_tests(
        cwd_path,
        gate=gate,
        round_index=round_index,
        test_commands=test_commands,
        timeout_s=test_timeout_s,
        runner=runner,
    )
    if test_receipt is not None:
        receipts.append(test_receipt)
        if test_receipt["status"] != "passed":
            failures.append("runtime_tests_failed")
    elif _claims_tests_passed(outcome_payload):
        failures.append("runtime_test_command_missing")
        receipts.append(_receipt(
            receipt_id=f"runtime-tests-{gate}-{round_index}",
            kind="test",
            status="failed",
            gate=gate,
            changed_files=[],
            claims=["tests passed"],
            extra={"reason": "runtime_test_command_missing"},
        ))

    details = {
        "gate": gate,
        "round_index": round_index,
        "baseline": baseline,
        "declared_changed_files": changed_files,
        "actual_changed_files": actual_changed_files,
        "test_commands": test_commands,
        "receipts": receipts,
    }
    probe = (
        ProbeResult("P11", "red", "runtime_evidence_failed", {**details, "failures": failures})
        if failures else
        ProbeResult("P11", "green", "runtime_evidence_verified", details)
    )
    return RuntimeEvidenceResult(
        probe=probe,
        receipts=tuple(receipts),
        event_payload={
            "kind": "dual_agent_runtime_evidence",
            "task_id": task_id,
            "run_id": run_id,
            "gate": gate,
            "round_index": round_index,
            "probe": {
                "probe_id": probe.probe_id,
                "status": probe.status,
                "reason": probe.reason,
                "details": probe.details,
            },
            "receipts": receipts,
        },
    )


def _receipt(
    *,
    receipt_id: str,
    kind: str,
    status: str,
    gate: str,
    changed_files: list[str],
    claims: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "receipt_id": receipt_id,
        "kind": kind,
        "status": status,
        "source": "supervisor",
        "evidence_grade": "runtime_native",
        "gate": gate,
        "changed_files": changed_files,
        "claims": claims or [],
    }
    payload.update(extra or {})
    return mark_supervisor_runtime_receipt(payload)


def _current_changed_files(
    cwd: Path,
    *,
    baseline_head: str | None = None,
    runner: Runner,
) -> dict[str, Any]:
    status = _run_git(cwd, ["status", "--porcelain=v1", "-uall"], runner=runner)
    if status.returncode != 0:
        return {
            "status": "failed",
            "reason": "git_status_unavailable",
            "changed_files": [],
            "name_status": [],
            "stderr": _tail(status.stderr),
        }
    entries: list[dict[str, str]] = []
    files: list[str] = []
    for line in status.stdout.splitlines():
        parsed = _parse_git_name_status_line(line, status_width=2)
        if parsed is None:
            continue
        code, path_text = parsed
        entries.append({"status": code, "path": path_text, "source": "worktree"})
        files.append(path_text)

    committed_entries: list[dict[str, str]] = []
    committed_files: list[str] = []
    if baseline_head:
        head = _run_git(cwd, ["rev-parse", "HEAD"], runner=runner)
        if head.returncode == 0 and head.stdout.strip() and head.stdout.strip() != baseline_head:
            committed = _run_git(
                cwd,
                ["diff", "--name-status", "--find-renames", baseline_head, "HEAD"],
                runner=runner,
            )
            if committed.returncode == 0:
                for line in committed.stdout.splitlines():
                    parsed = _parse_git_name_status_line(line, status_width=None)
                    if parsed is None:
                        continue
                    code, path_text = parsed
                    committed_entries.append({
                        "status": code,
                        "path": path_text,
                        "source": "committed_since_baseline",
                    })
                    committed_files.append(path_text)
            else:
                return {
                    "status": "failed",
                    "reason": "git_committed_diff_unavailable",
                    "changed_files": [],
                    "worktree_changed_files": sorted(dict.fromkeys(files)),
                    "committed_changed_files": [],
                    "name_status": entries,
                    "stderr": _tail(committed.stderr),
                }

    entries.extend(committed_entries)
    files.extend(committed_files)
    return {
        "status": "passed",
        "reason": "git_status_and_committed_diff_captured" if committed_entries else "git_status_captured",
        "changed_files": sorted(dict.fromkeys(files)),
        "worktree_changed_files": sorted(dict.fromkeys(
            entry["path"] for entry in entries if entry.get("source") == "worktree"
        )),
        "committed_changed_files": sorted(dict.fromkeys(committed_files)),
        "name_status": entries,
    }


def _parse_git_name_status_line(line: str, *, status_width: int | None) -> tuple[str, str] | None:
    if not line.strip():
        return None
    if status_width is None:
        parts = line.split("\t")
        if len(parts) < 2:
            return None
        code = parts[0].strip() or "changed"
        path_text = parts[-1].strip()
    else:
        code = line[:status_width].strip() or "changed"
        path_text = line[status_width + 1:].strip() if len(line) > status_width + 1 else line.strip()
    if " -> " in path_text:
        path_text = path_text.split(" -> ", 1)[1].strip()
    if not path_text:
        return None
    return code, path_text


def _deliverable_checks(cwd: Path, changed_files: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for relative in changed_files:
        path = cwd / relative
        if not path.exists():
            checks.append({"path": relative, "status": "failed", "reason": "runtime_deliverable_missing"})
            continue
        if not path.is_file():
            checks.append({"path": relative, "status": "failed", "reason": "runtime_deliverable_not_file"})
            continue
        size = path.stat().st_size
        if size <= 0:
            checks.append({"path": relative, "status": "failed", "reason": "runtime_deliverable_empty", "size": size})
            continue
        checks.append({"path": relative, "status": "passed", "reason": "runtime_deliverable_present", "size": size})
    return checks


def _run_declared_tests(
    cwd: Path,
    *,
    gate: str,
    round_index: int,
    test_commands: list[str],
    timeout_s: int,
    runner: Runner,
) -> dict[str, Any] | None:
    if not test_commands:
        return None
    workspace = _prepare_validation_copy(cwd)
    validation_cwd = Path(workspace["validation_cwd"])
    results: list[dict[str, Any]] = []
    try:
        for command in test_commands:
            started = time.monotonic()
            command_plan = _runtime_test_command_argv(command, validation_cwd)
            if command_plan["status"] != "allowed":
                results.append({
                    "command": command,
                    "argv": [],
                    "returncode": None,
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "reason": "runtime_test_command_rejected",
                    "rejection_reason": command_plan["reason"],
                })
                continue
            try:
                completed = runner(
                    command_plan["argv"],
                    cwd=str(validation_cwd),
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout_s)),
                    env=_runtime_test_env(validation_cwd),
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                reason = (
                    "runtime_test_environment_unavailable"
                    if completed.returncode != 0 and _pytest_environment_unavailable(completed)
                    else None
                )
                results.append({
                    "command": command,
                    "argv": command_plan["argv"],
                    "returncode": completed.returncode,
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "duration_ms": duration_ms,
                    **({"reason": reason} if reason else {}),
                    "stdout_tail": _tail(completed.stdout),
                    "stderr_tail": _tail(completed.stderr),
                })
            except FileNotFoundError as exc:
                results.append({
                    "command": command,
                    "argv": command_plan["argv"],
                    "returncode": None,
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "reason": "runtime_test_environment_unavailable",
                    "stderr_tail": _tail(str(exc)),
                })
            except subprocess.TimeoutExpired as exc:
                results.append({
                    "command": command,
                    "argv": command_plan["argv"],
                    "returncode": None,
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "reason": "runtime_test_timeout",
                    "stdout_tail": _tail(exc.stdout if isinstance(exc.stdout, str) else ""),
                    "stderr_tail": _tail(exc.stderr if isinstance(exc.stderr, str) else ""),
                })
    finally:
        shutil.rmtree(workspace["temp_parent"], ignore_errors=True)

    passed = all(result["status"] == "passed" for result in results)
    return _receipt(
        receipt_id=f"runtime-tests-{gate}-{round_index}",
        kind="test",
        status="passed" if passed else "failed",
        gate=gate,
        changed_files=[],
        claims=["tests passed"],
        extra={
            "commands": test_commands,
            "results": results,
            "isolated_worktree": True,
            "isolation_strategy": "copytree_current_worktree",
        },
    )


def _runtime_test_command_argv(command: str, validation_cwd: Path) -> dict[str, Any]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return {"status": "rejected", "reason": f"parse_error:{exc}", "argv": []}
    if not parts:
        return {"status": "rejected", "reason": "empty_command", "argv": []}
    if _contains_shell_metacharacters(parts):
        return {"status": "rejected", "reason": "shell_metacharacter", "argv": []}

    executable = Path(parts[0]).name
    if executable == "pytest":
        return {
            "status": "allowed",
            "reason": "pytest_module",
            "argv": [_runtime_python(validation_cwd), "-m", "pytest", *parts[1:]],
        }
    if executable == "python" or executable.startswith("python3"):
        if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest":
            return {
                "status": "allowed",
                "reason": "python_module_pytest",
                "argv": [_runtime_python(validation_cwd), "-m", "pytest", *parts[3:]],
            }
        if len(parts) >= 3 and parts[1] == "-m" and parts[2] == "cortex.vela_eval.runner":
            return {
                "status": "allowed",
                "reason": "python_module_vela_eval_runner",
                "argv": [_runtime_python(validation_cwd), "-m", "cortex.vela_eval.runner", *parts[3:]],
            }
        return {"status": "rejected", "reason": "python_non_pytest_module", "argv": []}
    if executable == "make":
        targets = parts[1:]
        if len(targets) == 1 and targets[0] in {
            "test",
            "tests",
            "pytest",
            "smoke-vela2-surface-truth",
        }:
            return {"status": "allowed", "reason": "make_test_target", "argv": ["make", targets[0]]}
        return {"status": "rejected", "reason": "make_target_not_allowlisted", "argv": []}
    return {"status": "rejected", "reason": "command_not_allowlisted", "argv": []}


def _runtime_python(validation_cwd: Path) -> str:
    venv_python = validation_cwd / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _contains_shell_metacharacters(parts: list[str]) -> bool:
    for part in parts:
        if part in _SHELL_META_TOKENS:
            return True
        if "`" in part or "$(" in part:
            return True
        if any(token in part for token in (";", "&&", "||", "|", ">", "<")):
            return True
    return False


def _runtime_test_env(validation_cwd: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "HOME": str(validation_cwd),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    venv_dir = validation_cwd / ".venv"
    path_parts = []
    if (venv_dir / "bin").exists():
        env["VIRTUAL_ENV"] = str(venv_dir)
        path_parts.append(str(venv_dir / "bin"))
    path_parts.extend([
        str(Path(sys.executable).parent),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ])
    env["PATH"] = os.pathsep.join(dict.fromkeys(path_parts))
    for key, value in os.environ.items():
        if key in env or _SECRET_ENV_KEY_RE.search(key):
            continue
        if key in {"TMPDIR", "TEMP", "TMP"} and value:
            env[key] = value
    return env


def _pytest_environment_unavailable(completed: subprocess.CompletedProcess[str]) -> bool:
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return (
        "no module named pytest" in output
        or "module pytest not found" in output
        or "pytest: command not found" in output
    )


def _prepare_validation_copy(cwd: Path) -> dict[str, Any]:
    temp_parent = Path(tempfile.mkdtemp(prefix="codex-runtime-evidence-"))
    validation_cwd = temp_parent / "worktree"

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "node_modules",
        }
        if Path(_directory).name == ".cortex":
            ignored.add("runtime_workspaces")
        return {name for name in names if name in ignored}

    shutil.copytree(cwd, validation_cwd, ignore=ignore)
    source_venv = cwd / ".venv"
    if source_venv.exists():
        try:
            os.symlink(source_venv, validation_cwd / ".venv", target_is_directory=True)
        except OSError:
            pass
    return {
        "temp_parent": temp_parent,
        "validation_cwd": validation_cwd,
    }


def _test_commands(value: Any, cwd: Path) -> list[str]:
    commands: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if not text:
            continue
        if _looks_like_command(text):
            commands.append(_normalise_python_command(text, cwd))
        else:
            target = _normalise_pytest_label(text, cwd)
            commands.append(f"{shlex.quote(sys.executable)} -m pytest {shlex.quote(target)} -q")
    return list(dict.fromkeys(commands))


def _normalise_python_command(text: str, cwd: Path) -> str:
    for executable in ("python3", "python"):
        if text == executable:
            return shlex.quote(sys.executable)
        if text.startswith(f"{executable} "):
            text = f"{shlex.quote(sys.executable)}{text[len(executable):]}"
            break
    return _normalise_pytest_targets(text, cwd)


_PYTEST_NODEID_LINE_SUFFIX_RE = re.compile(r"(?P<target>\S+\.py::\S+):(?P<line>\d+)(?=\s|$)")


def _normalise_pytest_targets(text: str, cwd: Path) -> str:
    if "pytest" not in text:
        return text
    text = _PYTEST_NODEID_LINE_SUFFIX_RE.sub(lambda match: match.group("target"), text)
    try:
        parts = shlex.split(text)
    except ValueError:
        return text

    if not any(part == "pytest" or part.endswith("/pytest") for part in parts):
        return text

    normalised: list[str] = []
    skip_next = False
    options_with_values = {
        "-c",
        "-k",
        "-m",
        "-o",
        "--basetemp",
        "--cache-clear",
        "--color",
        "--confcutdir",
        "--cov",
        "--cov-report",
        "--junit-xml",
        "--junitxml",
        "--maxfail",
        "--rootdir",
        "--tb",
    }
    for part in parts:
        if skip_next:
            normalised.append(part)
            skip_next = False
            continue
        normalised.append(_normalise_pytest_target_token(part, cwd))
        if part in options_with_values:
            skip_next = True
    return shlex.join(normalised)


_BARE_PYTEST_TARGET_RE = re.compile(
    r"^(?:(?P<class_name>[A-Za-z_][A-Za-z0-9_]*)::)?"
    r"(?P<test_name>test_[A-Za-z0-9_]+)(?P<params>\\[.*\\])?$"
)
_PYTEST_LABEL_TEST_RE = re.compile(r"\b(?P<test_name>test_[A-Za-z0-9_]+)(?!\.py)(?P<params>\[[^\]]+\])?\b")
_PYTEST_LABEL_FILE_RE = re.compile(r"\b(?P<path>(?:[\w.-]+/)*test_[\w.-]+\.py)(?::\d+)?\b")


def _normalise_pytest_label(text: str, cwd: Path) -> str:
    test_match = _PYTEST_LABEL_TEST_RE.search(text)
    if test_match:
        target = test_match.group("test_name") + (test_match.group("params") or "")
        resolved = _normalise_pytest_target_token(target, cwd)
        if resolved != target or "::" in resolved:
            return resolved
    file_match = _PYTEST_LABEL_FILE_RE.search(text)
    if file_match:
        return _normalise_pytest_file_label(file_match.group("path"), cwd)
    return _normalise_pytest_target_token(text, cwd)


def _normalise_pytest_file_label(path_text: str, cwd: Path) -> str:
    path = path_text.replace("\\", "/")
    candidates = [path]
    if "/" not in path:
        candidates.append(f"tests/{path}")
    for candidate in candidates:
        if (cwd / candidate).is_file():
            return candidate
    return path


def _normalise_pytest_target_token(target: str, cwd: Path) -> str:
    if not target or target.startswith("-"):
        return target
    if "/" in target or "\\" in target or ".py" in target:
        return _PYTEST_NODEID_LINE_SUFFIX_RE.sub(lambda match: match.group("target"), target)
    match = _BARE_PYTEST_TARGET_RE.match(target)
    if not match:
        return target
    lookup_key = target
    params = match.group("params") or ""
    if params:
        lookup_key = lookup_key[: -len(params)]
    matches = _pytest_target_index(cwd).get(lookup_key, [])
    if len(matches) != 1:
        return target
    return f"{matches[0]}{params}"


def _pytest_target_index(cwd: Path) -> dict[str, list[str]]:
    tests_dir = cwd / "tests"
    index: dict[str, list[str]] = {}
    if not tests_dir.exists():
        return index
    for path in sorted(tests_dir.rglob("test*.py")):
        if not path.is_file():
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        relative = path.relative_to(cwd).as_posix()
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                _append_index(index, node.name, f"{relative}::{node.name}")
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                        nodeid = f"{relative}::{node.name}::{child.name}"
                        _append_index(index, child.name, nodeid)
                        _append_index(index, f"{node.name}::{child.name}", nodeid)
    return index


def _append_index(index: dict[str, list[str]], key: str, value: str) -> None:
    bucket = index.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def _looks_like_command(text: str) -> bool:
    first = text.split(maxsplit=1)[0]
    return (
        first in {"python", "python3", "pytest", "make", "uv", "tox", "npm", "pnpm", "yarn"}
        or first.endswith("/python")
    )


def _claims_tests_passed(outcome_payload: dict[str, Any]) -> bool:
    chunks = _text_list(outcome_payload.get("claims"))
    chunks.extend(_text_list(outcome_payload.get("decisions")))
    chunks.append(str(outcome_payload.get("summary") or ""))
    text = "\n".join(chunks).lower()
    return "tests passed" in text or "test passed" in text


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _run_git(cwd: Path, args: list[str], *, runner: Runner) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _tail(value: str | bytes | None, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return value[-limit:]
