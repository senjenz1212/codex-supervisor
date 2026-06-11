"""Whitelisted live policy overlay for supervisor auto-evolution."""
from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

import yaml


POLICY_OVERLAY_SCHEMA_VERSION = "supervisor-policy-overlay/v1"
POLICY_OVERLAY_SNAPSHOT_SCHEMA_VERSION = "supervisor-policy-overlay-snapshot/v1"
POLICY_REGRESSION_SCHEMA_VERSION = "supervisor-policy-regression/v1"
POLICY_ROLLBACK_DRAFT_SCHEMA_VERSION = "supervisor-policy-rollback-draft/v1"
POLICY_OVERLAY_PATH = ".supervisor/policy-overlay.yaml"
POLICY_OVERLAY_BLOCK_HEADER = "Supervisor policy overlay guidance"
EMPTY_POLICY_OVERLAY_HASH = sha256(b"").hexdigest()
ALLOWED_OVERLAY_KEYS = frozenset({
    "schema_version",
    "active_proposal_id",
    "instruction_guidance_blocks",
    "lesson_limit",
    "rubric_thresholds",
})


class PolicyOverlayError(RuntimeError):
    """Raised when a policy overlay or target is outside the safe surface."""


@dataclass(frozen=True)
class PolicyOverlaySnapshot:
    path: str
    exists: bool
    content_hash: str
    proposal_id: str
    lesson_limit: int
    guidance_blocks: Mapping[str, Any]
    rubric_thresholds: Mapping[str, Any]
    raw: Mapping[str, Any]

    def instruction_block(self, *, gate: str) -> str:
        return render_policy_overlay_block(self.raw, gate=gate)

    def block_hash(self, *, gate: str) -> str:
        return sha256(self.instruction_block(gate=gate).encode("utf-8")).hexdigest()

    def to_event_payload(self, *, gate: str) -> dict[str, Any]:
        block = self.instruction_block(gate=gate)
        return {
            "schema_version": POLICY_OVERLAY_SNAPSHOT_SCHEMA_VERSION,
            "gate": gate,
            "overlay_path": self.path,
            "exists": self.exists,
            "overlay_hash": self.content_hash,
            "policy_overlay_hash": self.content_hash,
            "proposal_id": self.proposal_id,
            "policy_proposal_id": self.proposal_id,
            "lesson_limit": self.lesson_limit,
            "block": block,
            "block_sha256": sha256(block.encode("utf-8")).hexdigest(),
            "whitelisted_keys": sorted(ALLOWED_OVERLAY_KEYS),
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_authority": "unchanged",
        }


def normalise_overlay_target(path: str | Path, *, repo_root: str | Path) -> str:
    """Return a repo-relative path and reject non-overlay policy targets."""
    repo_root_path = Path(repo_root).expanduser().resolve()
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        raise PolicyOverlayError("policy overlay target path is required")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            raw = candidate.resolve(strict=False).relative_to(repo_root_path).as_posix()
        except ValueError as exc:
            raise PolicyOverlayError(f"policy overlay target is outside repo root: {path}") from exc
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PolicyOverlayError(f"policy overlay target traversal is not allowed: {path}")
        parts.append(part)
    rel = "/".join(parts)
    if rel != POLICY_OVERLAY_PATH:
        raise PolicyOverlayError(
            f"policy evolution may only target {POLICY_OVERLAY_PATH}; observed {rel}"
        )
    return rel


def load_policy_overlay(repo_root: str | Path) -> PolicyOverlaySnapshot:
    path = Path(repo_root).expanduser().resolve() / POLICY_OVERLAY_PATH
    if not path.exists():
        return PolicyOverlaySnapshot(
            path=POLICY_OVERLAY_PATH,
            exists=False,
            content_hash=EMPTY_POLICY_OVERLAY_HASH,
            proposal_id="",
            lesson_limit=5,
            guidance_blocks={},
            rubric_thresholds={},
            raw={},
        )
    if not path.is_file():
        raise PolicyOverlayError(f"policy overlay path is not a file: {POLICY_OVERLAY_PATH}")
    data = path.read_bytes()
    loaded = yaml.safe_load(data.decode("utf-8")) or {}
    if not isinstance(loaded, dict):
        raise PolicyOverlayError("policy overlay must be a mapping")
    unknown = sorted(set(str(key) for key in loaded) - ALLOWED_OVERLAY_KEYS)
    if unknown:
        raise PolicyOverlayError(f"policy overlay contains non-whitelisted keys: {unknown}")
    guidance = loaded.get("instruction_guidance_blocks") or {}
    if not isinstance(guidance, dict):
        raise PolicyOverlayError("instruction_guidance_blocks must be a mapping")
    rubric = loaded.get("rubric_thresholds") or {}
    if not isinstance(rubric, dict):
        raise PolicyOverlayError("rubric_thresholds must be a mapping")
    return PolicyOverlaySnapshot(
        path=POLICY_OVERLAY_PATH,
        exists=True,
        content_hash=sha256(data).hexdigest(),
        proposal_id=str(loaded.get("active_proposal_id") or ""),
        lesson_limit=_bounded_int(loaded.get("lesson_limit"), default=5, minimum=0, maximum=20),
        guidance_blocks=guidance,
        rubric_thresholds=rubric,
        raw=loaded,
    )


def apply_policy_overlay_to_instruction(
    instruction: str,
    *,
    overlay: PolicyOverlaySnapshot,
    gate: str,
) -> str:
    block = overlay.instruction_block(gate=gate)
    if not block or block in instruction:
        return instruction
    return instruction.rstrip() + "\n\n" + block


def render_policy_overlay_block(overlay: Mapping[str, Any], *, gate: str) -> str:
    guidance = overlay.get("instruction_guidance_blocks")
    if not isinstance(guidance, Mapping):
        return ""
    entries: list[str] = []
    for key in ("all", gate):
        value = guidance.get(key)
        if isinstance(value, str) and value.strip():
            entries.append(value.strip())
        elif isinstance(value, (list, tuple)):
            entries.extend(str(item).strip() for item in value if str(item).strip())
    if not entries:
        return ""
    lines = [
        POLICY_OVERLAY_BLOCK_HEADER,
        "This operator-approved overlay is advisory and cannot satisfy a gate by itself.",
    ]
    for index, entry in enumerate(entries, start=1):
        lines.append(f"{index}. {entry}")
    return "\n".join(lines)


def draft_policy_regression_rollback_if_needed(
    state: Any,
    *,
    run_id: str,
    proposal_id: str,
    rollback_pointer: Mapping[str, Any],
    task_class: str | None = None,
    gate: str | None = None,
    min_runs: int = 3,
    first_pass_drop_threshold: float = 0.05,
    false_accept_increase_threshold: float = 0.01,
    time_to_accept_increase_ratio: float = 0.25,
    now: int | None = None,
) -> dict[str, Any]:
    """Detect policy regression and draft exactly one rollback proposal."""
    proposal = str(proposal_id or "").strip()
    if not proposal:
        raise PolicyOverlayError("proposal_id is required for regression verification")
    _validate_rollback_pointer_targets(rollback_pointer)
    rows = state.list_quality_trend_rows(task_class=task_class, gate=gate)
    before = [row for row in rows if str(row.get("policy_proposal_id") or "") != proposal]
    after = [row for row in rows if str(row.get("policy_proposal_id") or "") == proposal]
    if len(after) < max(1, int(min_runs)) or not before:
        return {
            "status": "insufficient_data",
            "proposal_id": proposal,
            "after_count": len(after),
            "before_count": len(before),
            "rollback_drafted": False,
        }

    comparison = _compare_windows(before=before, after=after)
    reasons: list[str] = []
    if comparison["first_pass_rate_delta"] < -abs(float(first_pass_drop_threshold)):
        reasons.append("first_pass_rate_regressed")
    if comparison["false_accept_rate_delta"] > abs(float(false_accept_increase_threshold)):
        reasons.append("false_accept_rate_regressed")
    before_time = comparison["before_avg_time_to_accept_s"]
    after_time = comparison["after_avg_time_to_accept_s"]
    if before_time is not None and after_time is not None:
        allowed = before_time * (1.0 + max(0.0, float(time_to_accept_increase_ratio)))
        if after_time > allowed:
            reasons.append("time_to_accept_regressed")
    if not reasons:
        return {
            "status": "no_regression",
            "proposal_id": proposal,
            "comparison": comparison,
            "rollback_drafted": False,
        }

    existing = [
        event for event in state.read_events_since(run_id, after_event_id=0, limit=10_000)
        if event["kind"] == "autoresearch_policy_rollback_proposal_drafted"
        and str(event["payload"].get("proposal_id") or "") == proposal
    ]
    if existing:
        return {
            "status": "already_drafted",
            "proposal_id": proposal,
            "comparison": comparison,
            "rollback_drafted": True,
            "event_id": existing[-1]["event_id"],
        }

    timestamp = int(time.time()) if now is None else int(now)
    detection_payload = {
        "schema_version": POLICY_REGRESSION_SCHEMA_VERSION,
        "proposal_id": proposal,
        "task_class": task_class or "",
        "gate": gate or "",
        "reasons": reasons,
        "comparison": comparison,
        "detected_at": timestamp,
        "observational_only": True,
        "gate_authority": "unchanged",
    }
    detection_event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="policy_regression_detected",
        payload=detection_payload,
    )
    rollback_draft = {
        "schema_version": POLICY_ROLLBACK_DRAFT_SCHEMA_VERSION,
        "proposal_id": proposal,
        "status": "draft",
        "reason": "policy_regression_detected",
        "detected_event_id": detection_event_id,
        "rollback_pointer": dict(rollback_pointer),
        "comparison": comparison,
        "requires_operator_approval": True,
        "operator_approved": False,
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
        "gate_authority": "unchanged",
    }
    draft_event_id = state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_rollback_proposal_drafted",
        payload=rollback_draft,
    )
    return {
        "status": "rollback_drafted",
        "proposal_id": proposal,
        "comparison": comparison,
        "rollback_drafted": True,
        "policy_regression_event_id": detection_event_id,
        "rollback_draft_event_id": draft_event_id,
        "rollback_proposal": rollback_draft,
    }


def draft_policy_regression_rollbacks_for_trend_rows(
    state: Any,
    *,
    run_id: str,
    trend_rows: list[Mapping[str, Any]],
    min_runs: int = 3,
    first_pass_drop_threshold: float = 0.05,
    false_accept_increase_threshold: float = 0.01,
    time_to_accept_increase_ratio: float = 0.25,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Draft rollback proposals for newly recorded trend rows tied to live policy ids."""
    seen: set[tuple[str, str, str]] = set()
    results: list[dict[str, Any]] = []
    for row in trend_rows:
        proposal_id = str(row.get("policy_proposal_id") or "").strip()
        if not proposal_id:
            continue
        task_class = str(row.get("task_class") or "")
        gate = str(row.get("gate") or "")
        key = (proposal_id, task_class, gate)
        if key in seen:
            continue
        seen.add(key)
        rollback_pointer = _latest_rollback_pointer_for_proposal(state, proposal_id=proposal_id)
        if rollback_pointer is None:
            results.append({
                "status": "missing_rollback_pointer",
                "proposal_id": proposal_id,
                "task_class": task_class,
                "gate": gate,
                "rollback_drafted": False,
                "observational_only": True,
                "gate_authority": "unchanged",
            })
            continue
        result = draft_policy_regression_rollback_if_needed(
            state,
            run_id=run_id,
            proposal_id=proposal_id,
            rollback_pointer=rollback_pointer,
            task_class=task_class or None,
            gate=gate or None,
            min_runs=min_runs,
            first_pass_drop_threshold=first_pass_drop_threshold,
            false_accept_increase_threshold=false_accept_increase_threshold,
            time_to_accept_increase_ratio=time_to_accept_increase_ratio,
            now=now,
        )
        results.append({
            **result,
            "task_class": task_class,
            "gate": gate,
            "observational_only": True,
            "gate_authority": "unchanged",
        })
    return results


def _latest_rollback_pointer_for_proposal(state: Any, *, proposal_id: str) -> dict[str, Any] | None:
    list_approvals = getattr(state, "list_policy_proposal_approval_events", None)
    if list_approvals is None:
        return None
    events = list_approvals(proposal_id=proposal_id, limit=10_000)
    for event in reversed(events):
        payload = event.get("payload") if isinstance(event, Mapping) else {}
        pointer = payload.get("rollback_pointer") if isinstance(payload, Mapping) else None
        if isinstance(pointer, Mapping) and pointer.get("files"):
            return dict(pointer)
    return None


def _validate_rollback_pointer_targets(rollback_pointer: Mapping[str, Any]) -> None:
    files = rollback_pointer.get("files") if isinstance(rollback_pointer.get("files"), list) else []
    if not files:
        raise PolicyOverlayError("rollback pointer has no files")
    for item in files:
        if not isinstance(item, Mapping):
            raise PolicyOverlayError("rollback file entry must be an object")
        normalise_overlay_target(str(item.get("target_path") or ""), repo_root=Path.cwd())


def _compare_windows(*, before: list[Mapping[str, Any]], after: list[Mapping[str, Any]]) -> dict[str, Any]:
    before_first_pass = _rate(before, "first_pass_accepted")
    after_first_pass = _rate(after, "first_pass_accepted")
    before_false_accept = _false_accept_rate(before)
    after_false_accept = _false_accept_rate(after)
    before_time = _avg_time(before)
    after_time = _avg_time(after)
    return {
        "before_count": len(before),
        "after_count": len(after),
        "before_first_pass_rate": before_first_pass,
        "after_first_pass_rate": after_first_pass,
        "first_pass_rate_delta": after_first_pass - before_first_pass,
        "before_false_accept_rate": before_false_accept,
        "after_false_accept_rate": after_false_accept,
        "false_accept_rate_delta": after_false_accept - before_false_accept,
        "before_avg_time_to_accept_s": before_time,
        "after_avg_time_to_accept_s": after_time,
        "time_to_accept_delta_s": (
            after_time - before_time
            if before_time is not None and after_time is not None
            else None
        ),
    }


def _rate(rows: list[Mapping[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if bool(row.get(key))) / len(rows)


def _false_accept_rate(rows: list[Mapping[str, Any]]) -> float:
    denominator = sum(int(row.get("false_accept_denominator") or 0) for row in rows)
    if denominator <= 0:
        return 0.0
    false_count = sum(int(row.get("false_accept_count") or 0) for row in rows)
    return false_count / denominator


def _avg_time(rows: list[Mapping[str, Any]]) -> float | None:
    values = [
        float(row["time_to_accepted_outcome_s"])
        for row in rows
        if row.get("time_to_accepted_outcome_s") is not None
    ]
    return (sum(values) / len(values)) if values else None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        observed = int(value)
    except (TypeError, ValueError):
        observed = int(default)
    return max(minimum, min(maximum, observed))
