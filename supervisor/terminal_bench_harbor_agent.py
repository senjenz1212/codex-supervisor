"""Harbor custom agent wrapper for Terminal-Bench pilot runs."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .terminal_bench_eval import DEFAULT_MODEL


try:  # pragma: no cover - exercised only when Harbor is installed.
    from harbor.agents.base import AgentContext, BaseAgent
except Exception:  # pragma: no cover - local tests use the fallback.
    class AgentContext:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.n_input_tokens = None
            self.n_cache_tokens = None
            self.n_output_tokens = None
            self.cost_usd = None
            self.metadata = None

    class BaseAgent:  # type: ignore[no-redef]
        def __init__(self, logs_dir: Path, model_name: str | None = None, logger: Any | None = None, *args: Any, **kwargs: Any) -> None:
            self.logs_dir = Path(logs_dir)
            self.model_name = model_name
            self.logger = logger

        @classmethod
        def import_path(cls) -> str:
            return f"{cls.__module__}:{cls.__name__}"


class CodexSupervisorTerminalBenchAgent(BaseAgent):
    """Expose codex-supervisor as a Harbor agent import path.

    The default constructor is intentionally dry-run so imports and tests cannot
    launch a paid benchmark. The live pilot script passes ``dry_run=false`` only
    after its own budget guard has accepted the run.
    """

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: Any | None = None,
        dry_run: bool | str = True,
        max_budget_usd: float | str = 0.0,
        workflow_timeout_s: int | str = 900,
        agentic_lead_policy: str | None = None,
        min_subagents: int | str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(logs_dir=Path(logs_dir), model_name=model_name or DEFAULT_MODEL, logger=logger, *args, **kwargs)
        self.logs_dir = Path(logs_dir)
        self.model_name = model_name or DEFAULT_MODEL
        self.dry_run = _to_bool(dry_run)
        self.max_budget_usd = float(max_budget_usd or 0.0)
        self.workflow_timeout_s = int(workflow_timeout_s or 900)
        self.agentic_lead_policy = agentic_lead_policy
        self.min_subagents = None if min_subagents is None else int(min_subagents)
        if self.model_name != DEFAULT_MODEL:
            raise ValueError(f"Terminal-Bench pilot requires model {DEFAULT_MODEL}, got {self.model_name}")

    @staticmethod
    def name() -> str:
        return "codex-supervisor-terminal-bench"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: Any) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "setup.json").write_text(
            json.dumps({
                "agent": self.name(),
                "model": self.model_name,
                "dry_run": self.dry_run,
            }, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        started = time.monotonic()
        transcript: dict[str, Any] = {
            "agent": self.name(),
            "model": self.model_name,
            "dry_run": self.dry_run,
            "instruction_chars": len(instruction or ""),
            "environment_exec": None,
        }
        if self.dry_run:
            transcript["environment_exec"] = _safe_environment_probe(environment)
            status = "dry_run"
            cost_usd = 0.0
        else:
            status = self._run_live_bridge(instruction=instruction, environment=environment)
            cost_usd = self.max_budget_usd

        elapsed = time.monotonic() - started
        metadata = {
            "schema_version": "terminal-bench-harbor-agent-run/v1",
            "agent": self.name(),
            "model": self.model_name,
            "dry_run": self.dry_run,
            "workflow_status": status,
            "elapsed_s": round(elapsed, 6),
            "agentic_lead_policy": self.agentic_lead_policy,
            "min_subagents": self.min_subagents,
        }
        context.cost_usd = cost_usd
        context.metadata = metadata
        transcript["context"] = metadata
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "terminal_bench_agent_run.json").write_text(
            json.dumps(transcript, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _run_live_bridge(self, *, instruction: str, environment: Any) -> str:
        if self.max_budget_usd <= 0:
            raise RuntimeError("live Terminal-Bench harness run requires max_budget_usd > 0")
        # Keep the live integration explicit: the reporter measures Harbor
        # verifier outcomes, while this adapter only applies commands in the
        # sandbox. A full interactive terminal bridge is a follow-up if the dry
        # pilot scaffold is accepted.
        raise RuntimeError(
            "live codex-supervisor Terminal-Bench bridge is not enabled in the "
            "adapter by default; run the pilot script in dry-run/report mode or "
            "wire an approved terminal bridge before spending budget"
        )


def _safe_environment_probe(environment: Any) -> dict[str, Any]:
    if not hasattr(environment, "exec"):
        return {"status": "skipped", "reason": "environment_has_no_exec"}
    result = environment.exec("printf codex-supervisor-terminal-bench-agent", timeout_sec=10)
    return {
        "status": "ok" if getattr(result, "return_code", 1) == 0 else "failed",
        "return_code": getattr(result, "return_code", None),
        "stdout": getattr(result, "stdout", None),
        "stderr": getattr(result, "stderr", None),
    }


def _to_bool(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
