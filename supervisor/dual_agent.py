"""Dual-agent Slice 0 probe harness.

This module is intentionally deterministic. It does not spawn Codex, Claude,
Telegram, Cortex, or Codex Desktop. Slice 0 consumes fixture-shaped evidence
from those surfaces and decides whether the architecture is safe to build.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

from .redaction import redact, redact_for_telegram
from .state import State


HARD_STOP_PROBES = ("P0", "P1", "P2", "P3", "P4")
DESKTOP_VISIBILITY = "history_only"
BOUNDED_TEST_FOLLOWUP = "No tests appear to cover this change. Is this intentional?"
_BLOCKING_DECISION_TOKENS = (
    "deny",
    "denied",
    "block",
    "blocked",
    "revise",
    "reject",
    "rejected",
    "fail",
    "failed",
)
_ACCEPT_DECISION_TOKENS = ("accept", "accepted", "approve", "approved")


def _host(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.netloc or parsed.path).split("/")[0].lower()


_RAW_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]+?KEY-----", re.DOTALL),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)authorization\s*:\s*(?:bearer|basic)\s+(?!\[REDACTED)[^\s\"'}]+"),
    re.compile(r"(?i)bearer\s+(?!\[REDACTED)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)[A-Z][A-Z0-9_]*?(?:_KEY|_TOKEN|_SECRET|_PASSWORD|PASSWORD)"
        r"\s*[=:]\s*(?!\[REDACTED)[^\s\"'}]+"
    ),
)


def _public_secret_leaked(value: Any) -> bool:
    """Return true if raw secret patterns remain after redaction markers."""
    blob = json.dumps(value, sort_keys=True, default=str)
    return any(pattern.search(blob) for pattern in _RAW_SECRET_PATTERNS)


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    status: Literal["green", "red"]
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "green"


@dataclass(frozen=True)
class CredentialProbeInput:
    explicit_env: dict[str, str]
    ambient_env: dict[str, str]
    reported_request_host: str
    intended_gateway_host: str
    public_artifacts: dict[str, Any]
    required_env_keys: tuple[str, ...] = ("ANTHROPIC_BASE_URL",)


def evaluate_credential_boundary(probe: CredentialProbeInput) -> ProbeResult:
    """P0: env precedence, intended gateway, and public artifact redaction."""
    effective = dict(probe.ambient_env)
    effective.update(probe.explicit_env)

    missing_required = [
        key for key in probe.required_env_keys
        if not probe.explicit_env.get(key)
    ]
    if missing_required:
        return ProbeResult(
            "P0",
            "red",
            "missing_required_env",
            {"missing": missing_required},
        )

    intended_host = _host(probe.intended_gateway_host)
    reported_host = _host(probe.reported_request_host)
    if not intended_host or reported_host != intended_host:
        return ProbeResult(
            "P0",
            "red",
            "wrong_gateway",
            {"reported_host": reported_host, "intended_host": intended_host},
        )

    for key, value in probe.explicit_env.items():
        if effective.get(key) != value:
            return ProbeResult(
                "P0",
                "red",
                "env_precedence_failed",
                {"key": key},
            )

    public = redact(probe.public_artifacts)
    if _public_secret_leaked(public):
        return ProbeResult("P0", "red", "secret_leak_after_redaction")

    return ProbeResult("P0", "green", "credential_boundary_ok", {"effective_keys": sorted(effective)})


@dataclass(frozen=True)
class WorktreeProbeInput:
    approved_cwd: str
    process_cwd: str
    outcome_path: str
    touched_paths: tuple[str, ...]
    off_limits_paths: tuple[str, ...]
    expected_paths: tuple[str, ...]
    task_key_recorded: bool
    git_status_paths: tuple[str, ...] = ()


def _inside(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def evaluate_worktree_boundary(probe: WorktreeProbeInput) -> ProbeResult:
    """P1: spawned process and writes stay inside the approved worktree."""
    if Path(probe.process_cwd).resolve() != Path(probe.approved_cwd).resolve():
        return ProbeResult("P1", "red", "wrong_cwd")
    if not _inside(probe.outcome_path, probe.approved_cwd):
        return ProbeResult("P1", "red", "outcome_outside_worktree")
    touched_resolved = {str(Path(p).resolve()) for p in probe.touched_paths}
    expected_resolved = {str(Path(p).resolve()) for p in probe.expected_paths}
    outcome_resolved = str(Path(probe.outcome_path).resolve())
    if outcome_resolved not in touched_resolved:
        return ProbeResult("P1", "red", "outcome_not_written")
    missing_expected = sorted(expected_resolved - touched_resolved)
    if missing_expected:
        return ProbeResult("P1", "red", "missing_expected_paths", {"paths": missing_expected})
    for path in probe.touched_paths:
        if not _inside(path, probe.approved_cwd):
            return ProbeResult("P1", "red", "touched_outside_worktree", {"path": path})
        if str(Path(path).resolve()) not in expected_resolved:
            return ProbeResult("P1", "red", "unexpected_path_touched", {"path": path})
    off_limits = {str(Path(p).resolve()) for p in probe.off_limits_paths}
    touched = {str(Path(p).resolve()) for p in probe.touched_paths}
    if off_limits & touched:
        return ProbeResult("P1", "red", "off_limits_touched", {"paths": sorted(off_limits & touched)})
    git_paths = {str(Path(p).resolve()) for p in probe.git_status_paths}
    unexpected_git_paths = sorted(git_paths - expected_resolved)
    if unexpected_git_paths:
        return ProbeResult(
            "P1",
            "red",
            "unexpected_git_status",
            {"paths": unexpected_git_paths},
        )
    if not probe.task_key_recorded:
        return ProbeResult("P1", "red", "missing_task_key")
    return ProbeResult("P1", "green", "worktree_boundary_ok")


@dataclass(frozen=True)
class WorkerInvocationProbeInput:
    exit_code: int
    output_text: str
    captured_bytes: int
    expected_bytes: int
    specialist_names: tuple[str, ...]
    decisions: tuple[str, ...]
    objections: tuple[str, ...]
    spawned_by_codex: bool = False
    orchestration_surface: str = ""
    captured_token_estimate: int = 0
    expected_token_estimate: int = 5_000


def evaluate_worker_invocation(probe: WorkerInvocationProbeInput) -> ProbeResult:
    """P2: worker orchestration ran and preserved high-volume structure."""
    if not probe.spawned_by_codex:
        return ProbeResult("P2", "red", "not_codex_spawned")
    if not probe.orchestration_surface:
        return ProbeResult("P2", "red", "missing_orchestration_surface")
    if probe.exit_code != 0:
        return ProbeResult("P2", "red", "worker_exit_failed", {"exit_code": probe.exit_code})
    if probe.expected_bytes > 0 and probe.captured_bytes < probe.expected_bytes:
        return ProbeResult(
            "P2",
            "red",
            "output_truncated",
            {"captured_bytes": probe.captured_bytes, "expected_bytes": probe.expected_bytes},
        )
    if (
        probe.expected_token_estimate > 0
        and probe.captured_token_estimate < probe.expected_token_estimate
    ):
        return ProbeResult(
            "P2",
            "red",
            "load_case_too_small",
            {
                "captured_token_estimate": probe.captured_token_estimate,
                "expected_token_estimate": probe.expected_token_estimate,
            },
        )
    missing = [
        value
        for value in (*probe.specialist_names, *probe.decisions, *probe.objections)
        if value and value not in probe.output_text
    ]
    if missing:
        return ProbeResult("P2", "red", "missing_worker_signal", {"missing": missing})
    return ProbeResult(
        "P2",
        "green",
        "worker_orchestration_invocation_ok",
        {
            "orchestration_surface": probe.orchestration_surface,
            "captured_bytes": probe.captured_bytes,
            "captured_token_estimate": probe.captured_token_estimate,
        },
    )


class SpecialistRecord(BaseModel):
    name: str
    decision: str
    objection: str | None = None


class Outcome(BaseModel):
    task_id: str
    summary: str
    specialists: list[SpecialistRecord] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    changed_files: list[str] | None = None
    tests: list[str] | None = None
    test_status: Literal["passed", "failed", "unknown"] | None = None
    confidence: float | None = None
    confidence_rationale: str | None = None
    confidence_criteria: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    critical_review: dict[str, Any] = Field(default_factory=dict)

    @field_validator("test_status", mode="before")
    @classmethod
    def _normalize_test_status(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"pass", "passes", "passing"}:
            return "passed"
        if normalized in {"fail", "fails", "failing"}:
            return "failed"
        if normalized in {"not_run", "not_applicable", "n/a", "na", "none"}:
            return "unknown"
        return value

    @field_validator("decisions", "objections", "claims", "confidence_criteria", mode="before")
    @classmethod
    def _coerce_text_list(cls, value: Any) -> Any:
        # Leads/reviewers frequently emit these as objects (e.g.
        # {"decision": "accept", "rationale": ...} — the richer shape used by
        # dynamic_workflow synthesis) or as a bare scalar instead of list[str].
        # Normalise the SHAPE so a well-meant outcome parses instead of being
        # rejected as malformed. This does NOT change accept/block semantics:
        # P4 still reads the accept/block token from the normalised decision text.
        # (fix: prd_review blocked rejecting top-level `decisions` shape, 2026-06-06.)
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalised: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = ""
                for key in (
                    "decision", "verdict", "text", "objection",
                    "summary", "claim", "value",
                ):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        text = candidate.strip()
                        break
                if not text:
                    text = " ".join(
                        f"{k}: {v}"
                        for k, v in item.items()
                        if isinstance(v, (str, int, float))
                    ).strip()
            else:
                text = str(item).strip()
            if text:
                normalised.append(text)
        return normalised

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


_OUTCOME_RE = re.compile(
    r"<dual_agent_outcome>\s*(?P<json>\{.*?\})\s*</dual_agent_outcome>",
    re.DOTALL,
)


def parse_worker_outcome(transcript: str) -> Outcome:
    """Parse the structured outcome block from a worker transcript.

    Slice 0 deliberately requires an explicit block. Free prose can surround
    it, but the review-critical handoff is schema checked.
    """
    match = _OUTCOME_RE.search(transcript)
    if not match:
        raise ValueError("missing dual_agent_outcome block")
    try:
        payload = json.loads(match.group("json"))
        return Outcome(**payload)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"invalid dual_agent_outcome block: {e}") from e


def evaluate_outcome_fidelity(
    transcript: str,
    *,
    expected_specialists: Iterable[str],
    expected_decisions: Iterable[str],
    expected_objections: Iterable[str],
) -> tuple[ProbeResult, Outcome | None]:
    """P3: typed outcome preserves worker specialist signal."""
    try:
        outcome = parse_worker_outcome(transcript)
    except ValueError as e:
        return ProbeResult("P3", "red", str(e)), None

    specialist_names = {s.name for s in outcome.specialists}
    decisions = {*outcome.decisions, *(s.decision for s in outcome.specialists)}
    objections = {*outcome.objections, *(s.objection for s in outcome.specialists if s.objection)}
    missing_required_fields = [
        field_name for field_name, value in (
            ("changed_files", outcome.changed_files),
            ("tests", outcome.tests),
            ("test_status", outcome.test_status),
            ("confidence", outcome.confidence),
        )
        if value is None
    ]
    if missing_required_fields:
        return (
            ProbeResult(
                "P3",
                "red",
                "outcome_missing_required_fields",
                {"fields": missing_required_fields},
            ),
            outcome,
        )
    missing = {
        "specialists": sorted(set(expected_specialists) - specialist_names),
        "decisions": sorted(set(expected_decisions) - decisions),
        "objections": sorted(set(expected_objections) - objections),
    }
    if any(missing.values()):
        return ProbeResult("P3", "red", "outcome_signal_loss", missing), outcome
    return ProbeResult("P3", "green", "outcome_fidelity_ok"), outcome


def outcome_accepts(outcome: Outcome | dict[str, Any] | None) -> bool:
    """Return true only when acceptance is explicit and no blocking decision exists."""
    normalized = _outcome_dict(outcome)
    if normalized is None:
        return False
    decisions = _outcome_decision_texts(normalized)
    return any(_has_decision_token(item, _ACCEPT_DECISION_TOKENS) for item in decisions) and not any(
        _has_decision_token(item, _BLOCKING_DECISION_TOKENS) for item in decisions
    )


def evaluate_outcome_gate_decision(outcome: Outcome | dict[str, Any] | None) -> ProbeResult:
    """P4: lead cannot advance a gate when its typed outcome asks for revision."""
    normalized = _outcome_dict(outcome)
    if normalized is None:
        return ProbeResult("P4", "red", "missing_outcome_for_gate_decision")
    decisions = _outcome_decision_texts(normalized)
    blocking = [
        item
        for item in decisions
        if _has_decision_token(item, _BLOCKING_DECISION_TOKENS)
    ]
    if blocking:
        return ProbeResult(
            "P4",
            "red",
            "outcome_critical_review_blocked",
            {"blocking_decisions": blocking},
        )
    if not any(_has_decision_token(item, _ACCEPT_DECISION_TOKENS) for item in decisions):
        return ProbeResult(
            "P4",
            "red",
            "outcome_missing_accept_decision",
            {"decisions": decisions},
        )
    return ProbeResult("P4", "green", "outcome_gate_decision_ok", {"decisions": decisions})


def _outcome_dict(outcome: Outcome | dict[str, Any] | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    if isinstance(outcome, Outcome):
        return outcome.model_dump()
    if isinstance(outcome, dict):
        return outcome
    return None


def _outcome_decision_texts(outcome: dict[str, Any]) -> list[str]:
    decisions = [
        _normalise_decision_text(str(value))
        for value in outcome.get("decisions") or []
        if str(value).strip()
    ]
    for specialist in outcome.get("specialists") or []:
        if isinstance(specialist, dict) and str(specialist.get("decision") or "").strip():
            decisions.append(_normalise_decision_text(str(specialist.get("decision"))))
    critical_review = outcome.get("critical_review")
    if isinstance(critical_review, dict) and str(critical_review.get("decision") or "").strip():
        decisions.append(_normalise_decision_text(str(critical_review.get("decision"))))
    return decisions


def _normalise_decision_text(value: str) -> str:
    text = value.strip()
    match = re.match(
        r"^(accept|accepted|approve|approved|deny|denied|block|blocked|revise|reject|rejected|fail|failed)\s*[:\-–—]",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else text


def _has_decision_token(value: str, tokens: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(re.search(rf"\b{re.escape(token)}\b", lowered) for token in tokens)


@dataclass(frozen=True)
class GateRound:
    round_index: int
    codex_decision: Literal["accept", "deny", "revise"]
    claude_decision: Literal["accept", "deny", "revise"]
    codex_confidence: float
    claude_confidence: float
    objection: str


def evaluate_deadlock_budget(
    rounds: list[GateRound],
    *,
    per_gate_cap: int,
    task_budget: int,
    state: State | None = None,
    run_id: str = "dual-agent-slice0",
) -> ProbeResult:
    """P4: exhaustion pauses for human instead of choosing a winner."""
    cap = min(max(1, per_gate_cap), max(1, task_budget))
    exhausted = len(rounds) >= cap
    final_round = rounds[-1] if rounds else None
    accepted = (
        final_round is not None
        and final_round.codex_decision == "accept"
        and final_round.claude_decision == "accept"
    )
    if not exhausted or accepted:
        return ProbeResult("P4", "green", "not_deadlocked")

    action_id = None
    if state is not None:
        action_id = state.record_action(
            run_id=run_id,
            action_type="dual_agent_gate_deadlock",
            requested_by="dual_agent_gate",
            payload={
                "rounds": [r.__dict__ for r in rounds],
                "escalation_type": "kill_or_pause",
                "reason": "budget_exhausted",
            },
            status="paused_for_human",
        )
    return ProbeResult(
        "P4",
        "green",
        "paused_for_human",
        {"action_id": action_id, "escalation_type": "kill_or_pause"},
    )


def redact_artifacts(artifacts: dict[str, str]) -> dict[str, str]:
    return {name: redact_for_telegram(text) for name, text in artifacts.items()}


def evaluate_artifact_redaction(artifacts: dict[str, str]) -> ProbeResult:
    """P5: artifacts bound for disk/model/Telegram must be redacted."""
    redacted = redact_artifacts(artifacts)
    if _public_secret_leaked(redacted):
        return ProbeResult("P5", "red", "artifact_secret_leak")
    return ProbeResult("P5", "green", "artifact_redaction_ok", {"artifacts": redacted})


_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/).+(_test|\.test|\.spec)\.")


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def evaluate_test_coverage_gate(changed_files: list[str]) -> dict[str, Any]:
    """P6: ask a bounded follow-up when code changed without tests."""
    code_files = [
        p for p in changed_files
        if not is_test_path(p) and Path(p).suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}
    ]
    test_files = [p for p in changed_files if is_test_path(p)]
    if code_files and not test_files:
        return {
            "status": "needs_followup",
            "question": BOUNDED_TEST_FOLLOWUP,
            "max_tokens": 200,
            "rounds": 1,
            "counts_against_budget": False,
            "auto_deny": False,
        }
    return {"status": "ok", "question": None}


@dataclass(frozen=True)
class GateEvent:
    task_id: str
    kind: Literal["fyi", "alert", "approval", "milestone"]
    text: str
    ts: int


def plan_telegram_notifications(
    events: list[GateEvent],
    *,
    cooldown_s: int,
) -> list[dict[str, Any]]:
    """P7: batch FYIs while always sending alerts/approvals/milestones."""
    notifications: list[dict[str, Any]] = []
    pending_fyi: list[GateEvent] = []
    last_fyi_ts: dict[str, int] = {}

    def flush(task_id: str, ts: int) -> None:
        nonlocal pending_fyi
        batch = [e for e in pending_fyi if e.task_id == task_id]
        if not batch:
            return
        pending_fyi = [e for e in pending_fyi if e.task_id != task_id]
        last_fyi_ts[task_id] = ts
        notifications.append({
            "task_id": task_id,
            "kind": "fyi_batch",
            "send": True,
            "text": f"{len(batch)} routine gate updates suppressed into summary.",
            "events": [e.text for e in batch],
        })

    for event in sorted(events, key=lambda e: e.ts):
        if event.kind == "fyi":
            pending_for_task = [e for e in pending_fyi if e.task_id == event.task_id]
            last = last_fyi_ts.get(event.task_id)
            if (
                pending_for_task
                and last is not None
                and event.ts - last >= cooldown_s
            ):
                flush(event.task_id, event.ts)
                pending_fyi.append(event)
            else:
                pending_fyi.append(event)
            continue
        flush(event.task_id, event.ts)
        notifications.append({
            "task_id": event.task_id,
            "kind": event.kind,
            "send": True,
            "text": event.text,
        })

    for task_id in sorted({e.task_id for e in pending_fyi}):
        flush(task_id, max(e.ts for e in pending_fyi if e.task_id == task_id))
    return notifications


def validate_parallel_isolation(records: list[dict[str, Any]]) -> ProbeResult:
    """P10: every row/message/path must carry one unambiguous task id."""
    seen_paths: dict[str, str] = {}
    for record in records:
        task_id = str(record.get("task_id") or "")
        if not task_id:
            return ProbeResult("P10", "red", "missing_task_id", {"record": record})
        text = str(record.get("telegram_text") or "")
        if text and not text.startswith(f"[{task_id}]"):
            return ProbeResult("P10", "red", "telegram_missing_task_prefix", {"task_id": task_id})
        path = record.get("worktree")
        if path:
            resolved = str(Path(str(path)).resolve())
            other = seen_paths.get(resolved)
            if other and other != task_id:
                return ProbeResult("P10", "red", "shared_worktree", {"path": resolved})
            seen_paths[resolved] = task_id
    return ProbeResult("P10", "green", "parallel_isolation_ok")


_CLAIM_KEYWORDS = (
    "deployed to staging",
    "cron armed",
    "automation armed",
    "pr merged",
    "steer delivered",
)


def verify_outcome_claims(outcome: Outcome, evidence: dict[str, Any]) -> ProbeResult:
    """P11: agent claims must match ledger/tool evidence."""
    verified = {str(v).lower() for v in evidence.get("verified_claims", [])}
    unverified: list[str] = []
    for claim in outcome.claims:
        lowered = claim.lower()
        if any(keyword in lowered for keyword in _CLAIM_KEYWORDS) and lowered not in verified:
            unverified.append(claim)
    if unverified:
        return ProbeResult("P11", "red", "unverified_claims", {"claims": unverified})
    return ProbeResult("P11", "green", "claims_verified")


def slice0_hard_stop_summary(results: list[ProbeResult]) -> dict[str, Any]:
    by_id = {r.probe_id: r for r in results}
    missing = [probe_id for probe_id in HARD_STOP_PROBES if probe_id not in by_id]
    red = [
        probe_id for probe_id in HARD_STOP_PROBES
        if probe_id in by_id and not by_id[probe_id].ok
    ]
    if "P4" in by_id and by_id["P4"].ok and by_id["P4"].reason != "paused_for_human":
        red.append("P4")
    return {
        "status": "blocked" if missing or red else "ready_for_cs24",
        "missing": missing,
        "red": red,
        "desktop_visibility": DESKTOP_VISIBILITY,
    }
