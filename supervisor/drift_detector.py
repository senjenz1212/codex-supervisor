"""Four-layer drift detection cascade.

L1 (free)   - file-scope heuristic.
L2 (cheap)  - embedding similarity between task description and recent messages.
L3 (medium) - small-model plan-progress check.
L4 (gated)  - full adjudication via Claude Agent SDK (handled in agent_invoker).

Each layer is independent and short-circuits if the cheaper signal didn't fire.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from .config import Config
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
async def embed_similarity(task: str, recent_messages: list[str],
                            cfg: Config, oai: AsyncOpenAI) -> float:
    if not task or not recent_messages:
        return 1.0
    joined = "\n".join(recent_messages[-5:])[:4000]
    resp = await oai.embeddings.create(
        model=cfg.models.embedding_model,
        input=[task, joined],
    )
    a, b = resp.data[0].embedding, resp.data[1].embedding
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
        text = e.get("summary") or e.get("text") or e.get("content")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


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


async def plan_progress_check(task: str, plan: str, recent: list[dict],
                               cfg: Config, anthropic: AsyncAnthropic) -> dict:
    prompt = f"""Task given to the agent:
{task}

Plan the agent stated at the start:
{plan or '(no explicit plan captured)'}

Last 15 events (oldest first):
{json.dumps(recent[-15:], indent=2, default=str)[:6000]}

Output JSON only, conforming to this schema:
{json.dumps(PLAN_SCHEMA)}
"""
    resp = await anthropic.messages.create(
        model=cfg.models.drift_l3_model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    try:
        # Best-effort JSON extraction.
        start = text.index("{")
        return json.loads(text[start:text.rindex("}") + 1])
    except Exception as e:
        log.warning("plan-progress parse failed: %s; text=%r", e, text[:200])
        return {"plan_status": "unknown", "rationale": "parse_failed"}


# ---------- Orchestrator ----------
class DriftDetector:
    """Periodically runs L1-L3 on every active run. Enqueues L4 decisions when warranted."""

    def __init__(self, cfg: Config, state: State,
                 anthropic: AsyncAnthropic | None, oai: AsyncOpenAI | None):
        self.cfg = cfg
        self.state = state
        self.anthropic = anthropic
        self.oai = oai
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
            await self._check_one(run)

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
        if n_viol < self.cfg.drift.l1_scope_violation_threshold:
            return

        # L2
        messages = _intent_summaries(recent)
        if self.oai is None:
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L2",
                model=self.cfg.models.embedding_model,
                output={"skipped": True, "reason": "openai_client_unavailable"},
                latency_ms=0,
            )
            return
        t0 = time.monotonic()
        sim = await embed_similarity(task, messages, self.cfg, self.oai)
        self.state.write_verdict(
            run_id=run_id, phase="drift", layer="L2",
            model=self.cfg.models.embedding_model,
            output={"similarity": sim},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        if sim >= self.cfg.drift.l2_similarity_threshold:
            return

        # L3
        if self.anthropic is None:
            self.state.write_verdict(
                run_id=run_id, phase="drift", layer="L3",
                model=self.cfg.models.drift_l3_model,
                output={"skipped": True, "reason": "anthropic_client_unavailable"},
                latency_ms=0,
            )
            return
        plan = self._extract_plan(recent)
        t0 = time.monotonic()
        plan_check = await plan_progress_check(
            task, plan, recent, self.cfg, self.anthropic)
        self.state.write_verdict(
            run_id=run_id, phase="drift", layer="L3",
            model=self.cfg.models.drift_l3_model,
            output=plan_check,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        if plan_check.get("plan_status") not in ("abandoned", "blocked"):
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
            text = str(e.get("text") or e.get("content") or "")
            if "plan" in text.lower() or text.startswith("I'll") or text.startswith("Step"):
                return text[:2000]
        return ""
