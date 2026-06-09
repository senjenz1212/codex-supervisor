"""External no-mistakes validation adapter.

This module intentionally treats `no-mistakes` as an external executable. The
supervisor ledger remains the source of truth; no-mistakes output is evidence
for a post-acceptance validation step.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


NoMistakesPolicy = Literal["off", "advisory", "required", "shipping"]
NoMistakesVerdict = Literal[
    "accepted",
    "advisory_blocked",
    "required_blocked",
    "skipped",
    "unavailable",
    "changed_requires_rerun",
]
NoMistakesStatus = Literal["accepted", "blocked", "skipped", "failed"]

Runner = Callable[..., subprocess.CompletedProcess[str]]
SHIPPING_STEPS = ("push", "pr", "ci")
PASSING_OUTCOMES = {"checks-passed", "checks_passed", "passed", "success", "ok"}
BLOCKING_ACTIONS = {"auto-fix", "auto_fix", "ask-user", "ask_user"}


@dataclass(frozen=True)
class NoMistakesConfig:
    policy: NoMistakesPolicy = "off"
    binary: str = "no-mistakes"
    skip_steps: tuple[str, ...] = SHIPPING_STEPS
    auto_yes: bool = False
    timeout_s: int = 900
    require_clean_committed_branch: bool = True
    allow_shipping_steps: bool = False


@dataclass(frozen=True)
class NoMistakesFinding:
    finding_id: str
    severity: str = ""
    file: str = ""
    line: int | None = None
    description: str = ""
    action: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NoMistakesValidationRequest:
    cwd: str | Path
    task_id: str
    run_id: str
    intent: str
    config: NoMistakesConfig = field(default_factory=NoMistakesConfig)


@dataclass(frozen=True)
class NoMistakesValidationResult:
    task_id: str
    run_id: str
    policy: NoMistakesPolicy
    status: NoMistakesStatus
    verdict: NoMistakesVerdict
    reason: str
    command: tuple[str, ...]
    skip_steps: tuple[str, ...]
    auto_yes: bool
    exit_code: int | None
    wall_clock_s: float
    findings: tuple[NoMistakesFinding, ...] = ()
    stdout_tail: str = ""
    stderr_tail: str = ""
    changed_files_before: tuple[str, ...] = ()
    changed_files_after: tuple[str, ...] = ()
    head_before: str | None = None
    head_after: str | None = None
    output_ref: str | None = None
    output_sha256: str | None = None
    validation_cwd: str | None = None
    isolated_worktree: bool = False

    @property
    def blocking_findings(self) -> tuple[NoMistakesFinding, ...]:
        return tuple(
            finding for finding in self.findings
            if _normalise_action(finding.action) in BLOCKING_ACTIONS
            or finding.severity.lower() in {"error", "warning"}
        )

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "no-mistakes-validation/v1",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "policy": self.policy,
            "status": self.status,
            "verdict": self.verdict,
            "reason": self.reason,
            "command": list(self.command),
            "skip_steps": list(self.skip_steps),
            "auto_yes": self.auto_yes,
            "exit_code": self.exit_code,
            "wall_clock_s": self.wall_clock_s,
            "findings": [finding.to_dict() for finding in self.findings],
            "findings_count": len(self.findings),
            "blocking_findings": [
                finding.to_dict() for finding in self.blocking_findings
            ],
            "changed_files_before": list(self.changed_files_before),
            "changed_files_after": list(self.changed_files_after),
            "head_before": self.head_before,
            "head_after": self.head_after,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "output_ref": self.output_ref,
            "output_sha256": self.output_sha256,
            "validation_cwd": self.validation_cwd,
            "isolated_worktree": self.isolated_worktree,
        }

    def to_receipt(self) -> dict[str, Any]:
        receipt_status = (
            "passed" if self.verdict == "accepted"
            else "skipped" if self.verdict in {"skipped", "unavailable"}
            else "blocked"
        )
        return {
            "receipt_id": f"no-mistakes-{self.run_id}-{uuid.uuid5(uuid.NAMESPACE_URL, ':'.join(self.command))}",
            "kind": "no_mistakes_validation_receipt",
            "status": receipt_status,
            "no_mistakes_policy": self.policy,
            "verdict": self.verdict,
            "reason": self.reason,
            "command": list(self.command),
            "skip_steps": list(self.skip_steps),
            "findings_count": len(self.findings),
            "blocking_findings": [
                finding.to_dict() for finding in self.blocking_findings
            ],
            "output_ref": self.output_ref,
            "output_sha256": self.output_sha256,
            "changed_files_before": list(self.changed_files_before),
            "changed_files_after": list(self.changed_files_after),
            "head_before": self.head_before,
            "head_after": self.head_after,
            "validation_cwd": self.validation_cwd,
            "isolated_worktree": self.isolated_worktree,
        }


def run_no_mistakes_validation(
    request: NoMistakesValidationRequest,
    *,
    runner: Runner = subprocess.run,
) -> NoMistakesValidationResult:
    cfg = request.config
    command = build_no_mistakes_command(cfg, intent=request.intent)
    skip_steps = _effective_skip_steps(cfg)
    cwd = Path(request.cwd).expanduser()

    if cfg.policy == "off":
        return _result(
            request,
            status="skipped",
            verdict="skipped",
            reason="no_mistakes_policy_off",
            command=command,
            skip_steps=skip_steps,
        )

    preflight = _preflight_snapshot(cwd, require_clean=cfg.require_clean_committed_branch)
    if preflight["blocked"]:
        return _result(
            request,
            status="skipped" if cfg.policy == "advisory" else "blocked",
            verdict="unavailable" if cfg.policy == "advisory" else "required_blocked",
            reason=str(preflight["reason"]),
            command=command,
            skip_steps=skip_steps,
            changed_files_before=tuple(preflight["changed_files"]),
            changed_files_after=tuple(preflight["changed_files"]),
            head_before=preflight["head"],
            head_after=preflight["head"],
        )

    workspace = _prepare_validation_workspace(cwd, request=request)
    if workspace["blocked"]:
        return _result(
            request,
            status="skipped" if cfg.policy == "advisory" else "blocked",
            verdict="unavailable" if cfg.policy == "advisory" else "required_blocked",
            reason=str(workspace["reason"]),
            command=command,
            skip_steps=skip_steps,
            changed_files_before=tuple(preflight["changed_files"]),
            changed_files_after=tuple(preflight["changed_files"]),
            head_before=preflight["head"],
            head_after=preflight["head"],
        )

    validation_cwd = Path(workspace["validation_cwd"])
    validation_preflight = _snapshot(validation_cwd)
    started = time.monotonic()
    result: NoMistakesValidationResult | None = None
    try:
        completed = runner(
            list(command),
            cwd=str(validation_cwd),
            capture_output=True,
            text=True,
            timeout=cfg.timeout_s,
            check=False,
        )
    except FileNotFoundError:
        validation_postflight = _snapshot(validation_cwd)
        result = _result(
            request,
            status="skipped" if cfg.policy == "advisory" else "blocked",
            verdict="unavailable" if cfg.policy == "advisory" else "required_blocked",
            reason="no_mistakes_binary_unavailable",
            command=command,
            skip_steps=skip_steps,
            wall_clock_s=time.monotonic() - started,
            changed_files_before=tuple(validation_preflight["changed_files"]),
            changed_files_after=tuple(validation_postflight["changed_files"]),
            head_before=validation_preflight["head"],
            head_after=validation_postflight["head"],
            validation_cwd=str(validation_cwd),
            isolated_worktree=bool(workspace["isolated_worktree"]),
        )
    except subprocess.TimeoutExpired as exc:
        validation_postflight = _snapshot(validation_cwd)
        result = _result(
            request,
            status="failed",
            verdict="advisory_blocked" if cfg.policy == "advisory" else "required_blocked",
            reason="no_mistakes_timeout",
            command=command,
            skip_steps=skip_steps,
            exit_code=None,
            wall_clock_s=time.monotonic() - started,
            stdout_tail=_tail(_decode_maybe(exc.stdout)),
            stderr_tail=_tail(_decode_maybe(exc.stderr)),
            changed_files_before=tuple(validation_preflight["changed_files"]),
            changed_files_after=tuple(validation_postflight["changed_files"]),
            head_before=validation_preflight["head"],
            head_after=validation_postflight["head"],
            validation_cwd=str(validation_cwd),
            isolated_worktree=bool(workspace["isolated_worktree"]),
        )
    except Exception as exc:
        validation_postflight = _snapshot(validation_cwd)
        result = _result(
            request,
            status="failed",
            verdict="advisory_blocked" if cfg.policy == "advisory" else "required_blocked",
            reason="no_mistakes_runner_exception",
            command=command,
            skip_steps=skip_steps,
            exit_code=None,
            wall_clock_s=time.monotonic() - started,
            stderr_tail=_tail(f"{type(exc).__name__}: {exc}"),
            changed_files_before=tuple(validation_preflight["changed_files"]),
            changed_files_after=tuple(validation_postflight["changed_files"]),
            head_before=validation_preflight["head"],
            head_after=validation_postflight["head"],
            validation_cwd=str(validation_cwd),
            isolated_worktree=bool(workspace["isolated_worktree"]),
        )
    finally:
        if result is not None:
            _cleanup_validation_workspace(workspace)

    if result is not None:
        return result
    wall_clock_s = time.monotonic() - started
    postflight = _snapshot(validation_cwd)
    stdout = str(completed.stdout or "")
    stderr = str(completed.stderr or "")
    findings, outcome, gate = parse_no_mistakes_output(stdout)
    changed = (
        tuple(validation_preflight["changed_files"]) != tuple(postflight["changed_files"])
        or validation_preflight["head"] != postflight["head"]
    )
    if changed:
        verdict: NoMistakesVerdict = "changed_requires_rerun"
        status: NoMistakesStatus = "blocked"
        reason = "no_mistakes_changed_worktree_requires_rerun"
    elif findings or gate:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "blocked"
        reason = f"no_mistakes_gate_{gate}" if gate else "no_mistakes_findings"
    elif completed.returncode != 0:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "failed"
        reason = "no_mistakes_failed"
    elif not outcome or _normalise_action(outcome) in PASSING_OUTCOMES:
        verdict = "accepted"
        status = "accepted"
        reason = "no_mistakes_checks_passed"
    else:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "blocked"
        reason = f"no_mistakes_outcome_{outcome}"

    result = _result(
        request,
        status=status,
        verdict=verdict,
        reason=reason,
        command=command,
        skip_steps=skip_steps,
        exit_code=completed.returncode,
        wall_clock_s=wall_clock_s,
        findings=tuple(findings),
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        changed_files_before=tuple(validation_preflight["changed_files"]),
        changed_files_after=tuple(postflight["changed_files"]),
        head_before=validation_preflight["head"],
        head_after=postflight["head"],
        validation_cwd=str(validation_cwd),
        isolated_worktree=bool(workspace["isolated_worktree"]),
    )
    _cleanup_validation_workspace(workspace)
    return result


def build_no_mistakes_command(config: NoMistakesConfig, *, intent: str) -> tuple[str, ...]:
    command = [config.binary, "axi", "run", "--intent", intent]
    skip_steps = _effective_skip_steps(config)
    if skip_steps:
        command.append("--skip=" + ",".join(skip_steps))
    if _allow_auto_yes(config):
        command.append("--yes")
    return tuple(command)


def parse_no_mistakes_output(stdout: str) -> tuple[list[NoMistakesFinding], str, str]:
    findings: list[NoMistakesFinding] = []
    outcome = ""
    gate = ""
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("outcome:"):
            outcome = line.split(":", 1)[1].strip()
            continue
        if line.startswith("gate:"):
            gate = line.split(":", 1)[1].strip()
            continue
        if line.startswith("{") and "findings" in line:
            findings.extend(_findings_from_json_line(line))
            continue
        parsed = _finding_from_csvish_line(line)
        if parsed is not None:
            findings.append(parsed)
    return findings, outcome, gate


def _effective_skip_steps(config: NoMistakesConfig) -> tuple[str, ...]:
    seen: set[str] = set()
    steps: list[str] = []
    for step in config.skip_steps:
        normalized = str(step).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            steps.append(normalized)
    if not (config.policy == "shipping" and config.allow_shipping_steps):
        for step in SHIPPING_STEPS:
            if step not in seen:
                seen.add(step)
                steps.append(step)
    return tuple(steps)


def _allow_auto_yes(config: NoMistakesConfig) -> bool:
    return bool(
        config.auto_yes
        and config.policy == "shipping"
        and config.allow_shipping_steps
    )


def _preflight_snapshot(cwd: Path, *, require_clean: bool) -> dict[str, Any]:
    snapshot = _snapshot(cwd)
    if not require_clean:
        return {**snapshot, "blocked": False, "reason": ""}
    if snapshot["git_error"]:
        return {**snapshot, "blocked": True, "reason": "no_mistakes_git_preflight_failed"}
    if not snapshot["head"]:
        return {**snapshot, "blocked": True, "reason": "no_mistakes_no_committed_head"}
    if snapshot["changed_files"]:
        return {**snapshot, "blocked": True, "reason": "no_mistakes_dirty_worktree"}
    return {**snapshot, "blocked": False, "reason": ""}


def _snapshot(cwd: Path) -> dict[str, Any]:
    head_result = _run_git(cwd, "rev-parse", "HEAD")
    status_result = _run_git(cwd, "status", "--porcelain")
    changed_files: list[str] = []
    if status_result.returncode == 0:
        for line in status_result.stdout.splitlines():
            if not line.strip():
                continue
            changed_files.append(line[3:].strip() if len(line) > 3 else line.strip())
    return {
        "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "changed_files": tuple(sorted(changed_files)),
        "git_error": head_result.returncode != 0 or status_result.returncode != 0,
    }


def _prepare_validation_workspace(
    cwd: Path,
    *,
    request: NoMistakesValidationRequest,
) -> dict[str, Any]:
    if not request.config.require_clean_committed_branch:
        return {
            "blocked": False,
            "reason": "",
            "validation_cwd": cwd,
            "isolated_worktree": False,
            "temp_parent": None,
            "worktree_path": None,
            "repo_cwd": cwd,
        }
    temp_parent = Path(tempfile.mkdtemp(prefix="codex-no-mistakes-"))
    worktree_path = temp_parent / "worktree"
    add_result = _run_git(cwd, "worktree", "add", "--detach", str(worktree_path), "HEAD")
    if add_result.returncode != 0:
        shutil.rmtree(temp_parent, ignore_errors=True)
        return {
            "blocked": True,
            "reason": "no_mistakes_isolated_worktree_failed",
            "validation_cwd": cwd,
            "isolated_worktree": False,
            "temp_parent": None,
            "worktree_path": None,
            "repo_cwd": cwd,
            "stderr": _tail(add_result.stderr or ""),
        }
    return {
        "blocked": False,
        "reason": "",
        "validation_cwd": worktree_path,
        "isolated_worktree": True,
        "temp_parent": temp_parent,
        "worktree_path": worktree_path,
        "repo_cwd": cwd,
    }


def _cleanup_validation_workspace(workspace: dict[str, Any]) -> None:
    worktree_path = workspace.get("worktree_path")
    temp_parent = workspace.get("temp_parent")
    repo_cwd = workspace.get("repo_cwd")
    if worktree_path:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(repo_cwd) if repo_cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    if temp_parent:
        shutil.rmtree(Path(temp_parent), ignore_errors=True)


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _findings_from_json_line(line: str) -> list[NoMistakesFinding]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return []
    raw_findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(raw_findings, list):
        return []
    findings: list[NoMistakesFinding] = []
    for index, item in enumerate(raw_findings, start=1):
        if not isinstance(item, dict):
            continue
        findings.append(NoMistakesFinding(
            finding_id=str(item.get("id") or item.get("finding_id") or f"finding-{index}"),
            severity=str(item.get("severity") or ""),
            file=str(item.get("file") or item.get("path") or ""),
            line=_int_or_none(item.get("line")),
            description=str(item.get("description") or item.get("message") or ""),
            action=str(item.get("action") or ""),
            raw=json.dumps(item, sort_keys=True),
        ))
    return findings


def _finding_from_csvish_line(line: str) -> NoMistakesFinding | None:
    compact = line.lstrip("-* ").strip()
    if "," not in compact:
        return None
    parts = [part.strip() for part in compact.split(",")]
    if len(parts) < 5:
        return None
    if _normalise_action(parts[-1]) not in {"auto_fix", "ask_user", "no_op"}:
        return None
    return NoMistakesFinding(
        finding_id=parts[0],
        severity=parts[1],
        file=parts[2],
        description=",".join(parts[3:-1]).strip(),
        action=parts[-1],
        raw=line,
    )


def _result(
    request: NoMistakesValidationRequest,
    *,
    status: NoMistakesStatus,
    verdict: NoMistakesVerdict,
    reason: str,
    command: tuple[str, ...],
    skip_steps: tuple[str, ...],
    exit_code: int | None = None,
    wall_clock_s: float = 0.0,
    findings: tuple[NoMistakesFinding, ...] = (),
    stdout_tail: str = "",
    stderr_tail: str = "",
    changed_files_before: tuple[str, ...] = (),
    changed_files_after: tuple[str, ...] = (),
    head_before: str | None = None,
    head_after: str | None = None,
    validation_cwd: str | None = None,
    isolated_worktree: bool = False,
) -> NoMistakesValidationResult:
    return NoMistakesValidationResult(
        task_id=request.task_id,
        run_id=request.run_id,
        policy=request.config.policy,
        status=status,
        verdict=verdict,
        reason=reason,
        command=command,
        skip_steps=skip_steps,
        auto_yes=request.config.auto_yes,
        exit_code=exit_code,
        wall_clock_s=round(float(wall_clock_s), 6),
        findings=findings,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        changed_files_before=changed_files_before,
        changed_files_after=changed_files_after,
        head_before=head_before,
        head_after=head_after,
        validation_cwd=validation_cwd,
        isolated_worktree=isolated_worktree,
    )


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def _decode_maybe(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _normalise_action(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
