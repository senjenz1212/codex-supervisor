"""Four-layer drift detection with independent escalation signals.

L1 (free)   - file-scope heuristic.
L2 (cheap)  - embedding similarity between task description and recent messages.
L3 (medium) - small-model plan-progress check.
L4 (gated)  - full adjudication via AgentRuntime (handled in agent_invoker).

L1 never gates semantic checks. Any high-confidence scope, similarity,
plan-progress, repetition, tool-error, or no-progress signal may enqueue L4.
"""
from __future__ import annotations
import asyncio
from collections import Counter
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .config import Config
from .model_client import EmbeddingClient, ModelClient, ModelMessage, ModelRequest
from .scope_policy import evaluate_scope
from .state import State, Decision

log = logging.getLogger(__name__)
_INTENT_SUMMARY_KINDS = {"intent_summary", "item.intent_summary", "message.intent_summary"}


@dataclass
class DriftEvidence:
    scope_violations: int
    out_of_scope_paths: list[str]
    similarity: float | None
    plan_status: str | None     # on_plan | adjacent | blocked | exploratory | abandoned
    signals: list[str]
    repetition_count: int = 0
    tool_error_count: int = 0
    seconds_without_progress: int = 0


def _extract_scope(run_row, recent: list[dict]) -> list[str]:
    """Pick the scope hints from the registry, falling back to first-3-turn file references."""
    hints = []
    try:
        hints = json.loads(run_row["scope_hints"] or "[]")
    except Exception:
        pass
    if hints:
        return hints
    # Fallback: extract paths referenced in the first 10 events.
    paths: set[str] = set()
    for e in recent[:10]:
        for k in ("path", "file", "filename"):
            v = e.get(k)
            if isinstance(v, str):
                paths.add(v.split("/")[0] if "/" in v else v)
    return sorted(paths)


def _is_outside_scope(path: str, scope: list[str]) -> bool:
    return not any(path.startswith(s) for s in scope)


# ---------- Layer 1 ----------
def detect_scope_violations(recent: list[dict], scope: list[str]) -> tuple[int, list[str]]:
    writes = [e for e in recent if e.get("kind", "").endswith("file_change")
              or e.get("kind") == "patch"]
    out = []
    for w in writes:
        path = w.get("path") or w.get("file") or w.get("filename") or ""
        if path and _is_outside_scope(path, scope):
            out.append(path)
    return len(out), out


# ---------- Layer 2 ----------
async def embed_similarity(
    task: str,
    recent_messages: list[str],
    cfg: Config,
    embedding_client: EmbeddingClient,
) -> float:
    if not task or not recent_messages:
        return 1.0
    joined = "\n".join(recent_messages[-5:])[:4000]
    vectors = await embedding_client.embed(
        model=cfg.models.embedding_model,
        texts=[task, joined],
    )
    if len(vectors) != 2:
        raise ValueError("embedding client must return exactly two vectors")
    a, b = vectors
    if not a or not b or len(a) != len(b):
        raise ValueError("embedding client returned incompatible vectors")
    # Cosine — both vectors are unit-normalized by the API.
    return sum(x * y for x, y in zip(a, b))


def _intent_summaries(recent: list[dict]) -> list[str]:
    """Extract derived intent summaries for L2.

    Raw assistant/tool messages often contain logs, diffs, and boilerplate.
    L2 intentionally compares the original task to a compact derived stream.
    """
    out: list[str] = []
    for e in recent:
        if e.get("kind") not in _INTENT_SUMMARY_KINDS:
            continue
        text = _event_text(e)
        if text:
            out.append(text)
    if out:
        return out

    # Production rollouts do not yet emit a separate intent-summary stream.
    # Use normalized agent messages as a bounded fallback rather than silently
    # disabling L2 for every live run.
    for e in recent:
        if e.get("kind") != "agent.message":
            continue
        text = _event_text(e)
        if text:
            out.append(text)
    return out


def _event_text(event: dict[str, Any]) -> str:
    """Extract human-readable text from normalized or retained raw payloads."""
    containers: list[dict[str, Any]] = [event]
    payload = event.get("payload")
    if isinstance(payload, dict):
        containers.append(payload)
    message = event.get("message")
    if isinstance(message, dict):
        containers.append(message)
    for container in containers:
        for key in ("summary", "text", "message", "last_agent_message", "content"):
            text = _text_content(container.get(key))
            if text:
                return text
    return ""


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _semantic_heuristics(
    recent: list[dict[str, Any]],
    *,
    stall_threshold_s: int,
    now: float | None = None,
) -> dict[str, int | bool]:
    """Cheap high-confidence semantic signals independent of path scope."""
    messages = [
        _event_text(event).lower()
        for event in recent
        if event.get("kind") in {*_INTENT_SUMMARY_KINDS, "agent.message"}
        and _event_text(event)
    ]
    repetition_count = max(Counter(messages).values(), default=0)
    tool_error_count = sum(1 for event in recent if _tool_event_failed(event))

    progress_kinds = {
        "agent.message",
        "file_change",
        "item.file_change",
        "patch",
        "tool.completed",
        "turn.started",
    }
    progress_timestamps = [
        int(event.get("ts") or 0)
        for event in recent
        if event.get("kind") in progress_kinds and int(event.get("ts") or 0) > 0
    ]
    seconds_without_progress = 0
    if progress_timestamps:
        seconds_without_progress = max(
            0,
            int((now if now is not None else time.time()) - max(progress_timestamps)),
        )
    return {
        "loop_repetition": repetition_count >= 3,
        "tool_error": tool_error_count >= 2,
        "time_without_progress": (
            bool(progress_timestamps)
            and seconds_without_progress >= max(1, int(stall_threshold_s))
        ),
        "repetition_count": repetition_count,
        "tool_error_count": tool_error_count,
        "seconds_without_progress": seconds_without_progress,
    }


def _tool_event_failed(event: dict[str, Any]) -> bool:
    if event.get("kind") != "tool.completed":
        return False
    containers = [event]
    payload = event.get("payload")
    if isinstance(payload, dict):
        containers.append(payload)
    for container in containers:
        status = str(container.get("status") or container.get("state") or "").lower()
        if status in {"error", "failed", "failure", "cancelled", "canceled"}:
            return True
        if container.get("is_error") is True or container.get("error"):
            return True
        output = container.get("output")
        if isinstance(output, str) and re.search(
            r"(?:process exited with code|exit code|returncode)\s*[:=]?\s*[1-9]\d*",
            output,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def _has_plan_failure_hint(messages: list[str]) -> bool:
    text = "\n".join(messages).lower()
    return any(
        marker in text
        for marker in (
            "abandon",
            "no longer pursuing",
            "blocked on",
            "cannot continue",
            "can't continue",
        )
    )


# ---------- Layer 3 ----------
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "current_step": {"type": "string"},
        "plan_status": {"type": "string",
                        "enum": ["on_plan", "adjacent", "blocked", "exploratory", "abandoned"]},
        "rationale": {"type": "string"},
    },
    "required": ["plan_status", "rationale"],
}


async def plan_progress_check(
    task: str,
    plan: str,
    recent: list[dict],
    cfg: Config,
    model_client: ModelClient,
    *,
    timeout_s: float = 120.0,
) -> dict:
    prompt = f"""Task given to the agent:
{task}

Plan the agent stated at the start:
{plan or '(no explicit plan captured)'}

Last 15 events (oldest first):
{json.dumps(recent[-15:], indent=2, default=str)[:6000]}

Output JSON only, conforming to this schema:
{json.dumps(PLAN_SCHEMA)}
"""
    response = await asyncio.wait_for(
        model_client.complete(
            ModelRequest(
                model=cfg.models.drift_l3_model,
                messages=(ModelMessage(role="user", content=prompt),),
                max_tokens=400,
                temperature=0.0,
                metadata={
                    "purpose": "drift_plan_progress",
                    "response_schema": PLAN_SCHEMA,
                },
            )
        ),
        timeout_s,
    )
    text = response.text
    try:
        # Best-effort JSON extraction.
        start = text.index("{")
        return json.loads(text[start:text.rindex("}") + 1])
    except Exception as e:
        log.warning("plan-progress parse failed: %s; text=%r", e, text[:200])
        return {"plan_status": "unknown", "rationale": "parse_failed"}


# ---------- Orchestrator ----------
class DriftDetector:
    """Run independent L1-L3 signals and enqueue L4 when any one warrants it."""

    def __init__(
        self,
        cfg: Config,
        state: State,
        anthropic: ModelClient | None = None,
        oai: EmbeddingClient | None = None,
        *,
        model_client: ModelClient | None = None,
        embedding_client: EmbeddingClient | None = None,
    ):
        self.cfg = cfg
        self.state = state
        self.model_client = model_client if model_client is not None else anthropic
        self.embedding_client = (
            embedding_client if embedding_client is not None else oai
        )
        # Per-run cooldowns so we don't escalate repeatedly.
        self._last_l4: dict[str, float] = {}

    async def run(self) -> None:
        log.info("DriftDetector: starting check loop")
        while True:
            try:
                await self._tick()
            except Exception as e:
                log.exception("drift tick failed: %s", e)
            await asyncio.sleep(self.cfg.supervisor.drift_check_interval_s)

    async def _tick(self) -> None:
        for run in self.state.active_runs():
            try:
                await self._check_one(run)
            except Exception as e:
                log.exception(
                    "drift check failed for run %s: %s", run["run_id"], e
                )

    async def _check_one(self, run) -> None:
        run_id = run["run_id"]
        task = run["task"] or ""
        if not task:
            return  # nothing to compare against

        recent = self.state.recent_events(run_id, n=30)
        if len(recent) < 3:
            return

        # L1 — use the immutable scope_contract from the run_snapshot so that
        # L1 is consistent with the replay path (ticket 04 cycle 4 rewire).
        snapshot = self.state.get_run_snapshot(run_id)
        if snapshot is not None:
            scope_contract = json.loads(snapshot["scope_contract_json"])
        else:
            scope_contract = {}

        t0 = time.monotonic()
        findings = evaluate_scope(scope_contract, recent)
        n_viol = len(findings)
        self.state.write_verdict(
            run_id=run_id, phase="drift", layer="L1",
            model="heuristic",
            output={"scope_violations": n_viol, "findings": findings},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        scope_triggered = n_viol >= self.cfg.drift.l1_scope_violation_threshold
        heuristics = _semantic_heuristics(
            recent,
            stall_threshold_s=self.cfg.supervisor.stall_threshold_s,
        )

        # L2
        messages = _intent_summaries(recent)
        sim: float | None = None
        if self.embedding_client is None:
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L2",
                model=self.cfg.models.embedding_model,
                output={"skipped": True, "reason": "openai_client_unavailable"},
                latency_ms=0,
            )
        elif not messages:
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L2",
                model=self.cfg.models.embedding_model,
                output={"skipped": True, "reason": "semantic_messages_unavailable"},
                latency_ms=0,
            )
        else:
            t0 = time.monotonic()
            sim = await embed_similarity(
                task,
                messages,
                self.cfg,
                self.embedding_client,
            )
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L2",
                model=self.cfg.models.embedding_model,
                output={"similarity": sim},
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        similarity_triggered = (
            sim is not None
            and sim < self.cfg.drift.l2_similarity_threshold
        )

        # L3
        plan = self._extract_plan(recent)
        should_run_l3 = (
            similarity_triggered
            or _has_plan_failure_hint(messages)
            or bool(heuristics["loop_repetition"])
            or bool(heuristics["tool_error"])
            or bool(heuristics["time_without_progress"])
        )
        plan_check: dict[str, Any] = {
            "plan_status": None,
            "rationale": "semantic_precursor_not_triggered",
        }
        if should_run_l3 and self.model_client is None:
            plan_check = {
                "skipped": True,
                "reason": "anthropic_client_unavailable",
                "plan_status": None,
            }
            self.state.write_verdict(
                run_id=run_id,
                phase="drift",
                layer="L3",
                model=self.cfg.models.drift_l3_model,
                output=plan_check,
                latency_ms=0,
            )
        elif should_run_l3:
            t0 = time.monotonic()
            plan_check = await plan_progress_check(
                task,
                plan,
                recent,
                self.cfg,
                self.model_client,
                timeout_s=self.cfg.drift.l3_timeout_s,
            )
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L3",
                model=self.cfg.models.drift_l3_model,
                output=plan_check,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        plan_triggered = plan_check.get("plan_status") in ("abandoned", "blocked")
        signals: list[str] = []
        if scope_triggered:
            signals.append("scope_violation")
        if similarity_triggered:
            signals.append("goal_similarity")
        if plan_triggered:
            signals.append("plan_progress")
        for signal in ("loop_repetition", "tool_error", "time_without_progress"):
            if heuristics[signal]:
                signals.append(signal)
        if not signals:
            return

        # L4 — escalate to Agent SDK, but respect cooldown.
        now = time.time()
        if now - self._last_l4.get(run_id, 0) < self.cfg.supervisor.nudge_cooldown_s:
            log.info("drift L4 suppressed by cooldown for %s", run_id)
            return
        self._last_l4[run_id] = now

        await self.state.enqueue_decision(Decision(
            kind="adjudicate_drift",
            run_id=run_id,
            payload={
                "task": task,
                "scope": scope_contract,
                "evidence": DriftEvidence(
                    scope_violations=n_viol,
                    out_of_scope_paths=[
                        str(f.get("path"))
                        for f in findings
                        if f.get("path")
                    ],
                    similarity=sim,
                    plan_status=plan_check.get("plan_status"),
                    signals=signals,
                    repetition_count=int(heuristics["repetition_count"]),
                    tool_error_count=int(heuristics["tool_error_count"]),
                    seconds_without_progress=int(
                        heuristics["seconds_without_progress"]
                    ),
                ).__dict__,
                "plan": plan,
                "recent_events": recent,
            },
        ))
        log.info("drift L4 escalated for run %s", run_id)

    @staticmethod
    def _extract_plan(recent: list[dict]) -> str:
        """Pull the agent's first plan-like message from the run, if any."""
        for e in recent[:8]:
            text = _event_text(e)
            if "plan" in text.lower() or text.startswith("I'll") or text.startswith("Step"):
                return text[:2000]
        return ""
