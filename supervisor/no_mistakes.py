"""External no-mistakes validation adapter.

This module intentionally treats `no-mistakes` as an external executable. The
supervisor ledger remains the source of truth; no-mistakes output is evidence
for a post-acceptance validation step.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
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
    artifact_stash: dict[str, Any] | None = None

    def finish(result: NoMistakesValidationResult) -> NoMistakesValidationResult:
        restore = _restore_workflow_artifact_stash(cwd, artifact_stash)
        if not restore["blocked"]:
            return result
        return _result(
            request,
            status="blocked",
            verdict="advisory_blocked" if cfg.policy == "advisory" else "required_blocked",
            reason=str(restore["reason"]),
            command=command,
            skip_steps=skip_steps,
            exit_code=result.exit_code,
            wall_clock_s=result.wall_clock_s,
            findings=result.findings,
            stdout_tail=result.stdout_tail,
            stderr_tail=_tail("\n".join(
                item for item in [result.stderr_tail, str(restore.get("stderr") or "")]
                if item
            )),
            changed_files_before=result.changed_files_before,
            changed_files_after=tuple(_snapshot(cwd)["changed_files"]),
            head_before=result.head_before,
            head_after=result.head_after,
            validation_cwd=result.validation_cwd,
            isolated_worktree=result.isolated_worktree,
        )

    if cfg.policy == "off":
        return finish(_result(
            request,
            status="skipped",
            verdict="skipped",
            reason="no_mistakes_policy_off",
            command=command,
            skip_steps=skip_steps,
        ))

    preflight = _preflight_snapshot(cwd, require_clean=cfg.require_clean_committed_branch)
    if preflight["blocked"] and preflight["reason"] == "no_mistakes_dirty_worktree":
        artifact_stash = _stash_workflow_artifacts_if_safe(
            cwd,
            request=request,
            changed_files=tuple(preflight["changed_files"]),
        )
        if artifact_stash["blocked"]:
            return finish(_result(
                request,
                status="skipped" if cfg.policy == "advisory" else "blocked",
                verdict="unavailable" if cfg.policy == "advisory" else "required_blocked",
                reason=str(artifact_stash["reason"]),
                command=command,
                skip_steps=skip_steps,
                changed_files_before=tuple(preflight["changed_files"]),
                changed_files_after=tuple(preflight["changed_files"]),
                head_before=preflight["head"],
                head_after=preflight["head"],
            ))
        if artifact_stash["stashed"]:
            preflight = _preflight_snapshot(cwd, require_clean=cfg.require_clean_committed_branch)
    if preflight["blocked"]:
        return finish(_result(
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
        ))

    workspace = _prepare_validation_workspace(cwd, request=request)
    if workspace["blocked"]:
        return finish(_result(
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
        ))

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
        return finish(result)
    wall_clock_s = time.monotonic() - started
    postflight = _snapshot(validation_cwd)
    stdout = str(completed.stdout or "")
    stderr = str(completed.stderr or "")
    findings, outcome, gate = parse_no_mistakes_output(stdout)
    stashed_prefix = (
        str(artifact_stash.get("artifact_prefix") or "")
        if artifact_stash and artifact_stash.get("stashed")
        else ""
    )
    preflight_changes = _filter_changes_outside_prefix(
        validation_preflight["changed_files"], stashed_prefix
    )
    postflight_changes = _filter_changes_outside_prefix(
        postflight["changed_files"], stashed_prefix
    )
    changed = (
        preflight_changes != postflight_changes
        or validation_preflight["head"] != postflight["head"]
    )
    passing_outcome = bool(outcome) and _normalise_action(outcome) in PASSING_OUTCOMES
    if changed:
        verdict: NoMistakesVerdict = "changed_requires_rerun"
        status: NoMistakesStatus = "blocked"
        reason = "no_mistakes_changed_worktree_requires_rerun"
    elif findings:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "blocked"
        reason = f"no_mistakes_gate_{gate}" if gate else "no_mistakes_findings"
    elif completed.returncode != 0:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "failed"
        reason = "no_mistakes_failed"
    elif passing_outcome:
        verdict = "accepted"
        status = "accepted"
        reason = "no_mistakes_checks_passed"
    else:
        verdict = "advisory_blocked" if cfg.policy == "advisory" else "required_blocked"
        status = "blocked"
        reason = f"no_mistakes_gate_{gate}" if gate else "no_mistakes_missing_outcome"

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
    return finish(result)


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
    stripped = stdout.strip()
    if _looks_like_structured_no_mistakes(stripped):
        parsed = _structured_no_mistakes_payload(stripped)
        if parsed is None:
            return (
                [
                    NoMistakesFinding(
                        finding_id="no-mistakes-malformed-json",
                        severity="error",
                        description="no-mistakes structured JSON contract could not be parsed",
                        action="ask-user",
                        raw=_tail(stripped),
                    )
                ],
                "",
                "no_mistakes_structured_contract",
            )
        return parsed
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
        if line.startswith("step:") and not gate:
            gate = line.split(":", 1)[1].strip()
            continue
        if _looks_like_structured_no_mistakes(line):
            parsed = _structured_no_mistakes_payload(line)
            if parsed is None:
                findings.append(NoMistakesFinding(
                    finding_id="no-mistakes-malformed-json",
                    severity="error",
                    description="no-mistakes structured JSON contract could not be parsed",
                    action="ask-user",
                    raw=_tail(line),
                ))
                gate = gate or "no_mistakes_structured_contract"
                continue
            structured_findings, structured_outcome, structured_gate = parsed
            findings.extend(structured_findings)
            outcome = outcome or structured_outcome
            gate = gate or structured_gate
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


def _stash_workflow_artifacts_if_safe(
    cwd: Path,
    *,
    request: NoMistakesValidationRequest,
    changed_files: tuple[str, ...],
) -> dict[str, Any]:
    if not changed_files:
        return {"blocked": False, "stashed": False, "stash_ref": None}
    prefix = _workflow_artifact_prefix(request.task_id)
    if prefix is None:
        return {"blocked": True, "reason": "no_mistakes_dirty_worktree"}
    if not all(_is_under_prefix(path, prefix) for path in changed_files):
        return {"blocked": True, "reason": "no_mistakes_dirty_worktree"}

    marker = f"codex-supervisor:no-mistakes-artifacts:{request.run_id}:{uuid.uuid4()}"
    before = _stash_refs(cwd)
    push = _run_git(
        cwd,
        "stash",
        "push",
        "--include-untracked",
        "-m",
        marker,
        "--",
        str(prefix),
    )
    if push.returncode != 0:
        return {
            "blocked": True,
            "reason": "no_mistakes_artifact_stash_failed",
            "stderr": _tail(push.stderr or push.stdout or ""),
        }
    after = _stash_refs(cwd)
    new_refs = [item for item in after if item not in before and marker in item[1]]
    if not new_refs:
        return {"blocked": False, "stashed": False, "stash_ref": None}
    return {
        "blocked": False,
        "stashed": True,
        "stash_ref": new_refs[0][0],
        "marker": marker,
        "artifact_prefix": str(prefix),
    }


def _restore_workflow_artifact_stash(
    cwd: Path,
    stash: dict[str, Any] | None,
) -> dict[str, Any]:
    if not stash or not stash.get("stashed"):
        return {"blocked": False}
    stash_ref = str(stash.get("stash_ref") or "")
    if not stash_ref:
        return {"blocked": True, "reason": "no_mistakes_artifact_restore_failed"}
    prefix = stash.get("artifact_prefix")
    prefix_path = str(prefix) if prefix else ""
    apply_result = _run_git(cwd, "stash", "apply", stash_ref)
    if apply_result.returncode != 0:
        rollback_stderr = _rollback_failed_stash_apply(cwd, prefix_path, stash_ref)
        return {
            "blocked": True,
            "reason": "no_mistakes_artifact_restore_failed",
            "stderr": _tail(
                "\n".join(
                    item
                    for item in [
                        apply_result.stderr or apply_result.stdout or "",
                        rollback_stderr,
                    ]
                    if item
                )
            ),
        }
    _run_git(cwd, "stash", "drop", stash_ref)
    return {"blocked": False}


def _rollback_failed_stash_apply(
    cwd: Path,
    prefix_path: str,
    stash_ref: str,
) -> str:
    """Undo a half-applied `git stash apply` so the worktree is not left dirty.

    On conflict, ``git stash apply`` leaves merge markers in the working tree
    and keeps the stash on the stash list. Roll back any partial writes and
    drop the stash so the caller does not strand the worktree.
    """
    errors: list[str] = []
    paths = [prefix_path] if prefix_path else ["--", "."]
    checkout_result = _run_git(cwd, "checkout", "--", *paths)
    if checkout_result.returncode != 0:
        errors.append(checkout_result.stderr or checkout_result.stdout or "")
    clean_result = _run_git(cwd, "clean", "-fd", *([prefix_path] if prefix_path else []))
    if clean_result.returncode != 0:
        errors.append(clean_result.stderr or clean_result.stdout or "")
    drop_result = _run_git(cwd, "stash", "drop", stash_ref)
    if drop_result.returncode != 0:
        errors.append(drop_result.stderr or drop_result.stdout or "")
    return "\n".join(error for error in errors if error)


def _workflow_artifact_prefix(task_id: str) -> Path | None:
    task = str(task_id or "").strip()
    if not task:
        return None
    task_path = Path(task)
    if task_path.is_absolute() or any(part in {"", ".", ".."} for part in task_path.parts):
        return None
    return Path("docs") / "dual-agent" / task_path


def _is_under_prefix(path: str, prefix: Path) -> bool:
    try:
        Path(path).relative_to(prefix)
        return True
    except ValueError:
        return Path(path) == prefix


def _filter_changes_outside_prefix(
    changed_files: tuple[str, ...],
    prefix_path: str,
) -> tuple[str, ...]:
    if not prefix_path:
        return tuple(changed_files)
    prefix = Path(prefix_path)
    return tuple(path for path in changed_files if not _is_under_prefix(path, prefix))


def _stash_refs(cwd: Path) -> list[tuple[str, str]]:
    result = _run_git(cwd, "stash", "list", "--format=%gd%x00%s")
    refs: list[tuple[str, str]] = []
    if result.returncode != 0:
        return refs
    for line in result.stdout.splitlines():
        if "\0" not in line:
            continue
        ref, subject = line.split("\0", 1)
        refs.append((ref, subject))
    return refs


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
    del request
    return {
        "blocked": False,
        "reason": "",
        "validation_cwd": cwd,
        "isolated_worktree": False,
        "temp_parent": None,
        "worktree_path": None,
        "branch_name": None,
        "repo_cwd": cwd,
    }


def _cleanup_validation_workspace(workspace: dict[str, Any]) -> None:
    worktree_path = workspace.get("worktree_path")
    temp_parent = workspace.get("temp_parent")
    repo_cwd = workspace.get("repo_cwd")
    branch_name = workspace.get("branch_name")
    if worktree_path:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(repo_cwd) if repo_cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    if repo_cwd and branch_name:
        subprocess.run(
            ["git", "branch", "-D", str(branch_name)],
            cwd=str(repo_cwd),
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


def _looks_like_structured_no_mistakes(text: str) -> bool:
    if not text.startswith("{"):
        return False
    markers = ("schema_version", "no_mistakes", "no-mistakes", "findings", "outcome", "gate")
    return any(marker in text for marker in markers)


def _structured_no_mistakes_payload(text: str) -> tuple[list[NoMistakesFinding], str, str] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    schema = str(payload.get("schema_version") or "")
    if schema and not schema.startswith(("no-mistakes", "no_mistakes")):
        return None
    outcome = str(payload.get("outcome") or payload.get("verdict") or "")
    gate = str(payload.get("gate") or "")
    findings = _findings_from_json_payload(payload)
    if not outcome and _structured_contract_is_incomplete(payload, findings):
        return (
            [
                NoMistakesFinding(
                    finding_id="no-mistakes-incomplete-contract",
                    severity="error",
                    description=(
                        "no-mistakes structured JSON must include an outcome/verdict "
                        "or substantive findings"
                    ),
                    action="ask-user",
                    raw=json.dumps(payload, sort_keys=True),
                )
            ],
            "",
            gate or "no_mistakes_structured_contract",
        )
    return (
        findings,
        outcome,
        gate,
    )


def _structured_contract_is_incomplete(
    payload: dict[str, Any],
    findings: list[NoMistakesFinding],
) -> bool:
    if "findings" not in payload:
        return True
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return False
    return not findings


def _findings_from_json_payload(payload: dict[str, Any]) -> list[NoMistakesFinding]:
    raw_findings = payload.get("findings")
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        return [
            NoMistakesFinding(
                finding_id="no-mistakes-invalid-findings",
                severity="error",
                description="no-mistakes structured JSON findings must be an array",
                action="ask-user",
                raw=json.dumps(payload, sort_keys=True),
            )
        ]
    findings: list[NoMistakesFinding] = []
    for index, item in enumerate(raw_findings, start=1):
        if not isinstance(item, dict):
            findings.append(NoMistakesFinding(
                finding_id=f"finding-{index}",
                severity="error",
                description="no-mistakes structured JSON finding must be an object",
                action="ask-user",
                raw=json.dumps(item, sort_keys=True),
            ))
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
    try:
        parts = [part.strip() for part in next(csv.reader([compact]))]
    except csv.Error:
        parts = [part.strip() for part in compact.split(",")]
    if len(parts) < 5:
        return None
    action_index = -1
    if _normalise_action(parts[-1]) in {"auto_fix", "ask_user", "no_op"}:
        action_index = len(parts) - 1
    elif len(parts) >= 4 and _normalise_action(parts[3]) in {"auto_fix", "ask_user", "no_op"}:
        action_index = 3
    if action_index < 0:
        return None
    description_parts = parts[3:action_index] + parts[action_index + 1:]
    return NoMistakesFinding(
        finding_id=parts[0],
        severity=parts[1],
        file=parts[2],
        description=", ".join(description_parts).strip(),
        action=parts[action_index],
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
