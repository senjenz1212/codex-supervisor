"""AXI-style CLI for codex-supervisor durable workflow orchestration.

The CLI is intentionally a thin adapter over the same API used by the MCP
tools. It never drives workflow phases; the detached dispatcher is the only
process that writes request files or spawns workflow workers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.config import Config
from supervisor.redaction import redact
from supervisor.state import State


DEFAULT_CONFIG = str(Path.home() / ".codex-supervisor" / "config.yaml")
DEFAULT_JOB_FIELDS = ("job_id", "status", "recovery_point", "task_id")
DEFAULT_EVENT_FIELDS = ("event_id", "kind", "ts", "payload")
DEFAULT_GATE_FIELDS = ("gate", "status", "attempt_count", "task_id")
DEFAULT_TREND_FIELDS = ("task_class", "gate", "run_count", "first_pass_acceptance_rate")
DEFAULT_LESSON_FIELDS = ("task_class", "gate", "taxonomy_code", "root_cause")


class AxiUsageError(ValueError):
    pass


class AxiArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AxiUsageError(message)


def _read_json_arg(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    text = Path(value[1:]).expanduser().read_text(encoding="utf-8") if value.startswith("@") else value
    return json.loads(text)


def _split_fields(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    fields = tuple(part.strip() for part in raw.split(",") if part.strip())
    return fields or None


def _truncate(value: Any, *, max_chars: int = 180) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(redact(value), sort_keys=True, separators=(",", ":"))
    elif value is None:
        text = "null"
    else:
        text = str(value)
    if len(text) <= max_chars:
        return text
    keep = max(20, max_chars - 22)
    return f"{text[:keep]}...<+{len(text) - keep} chars>"


def _project(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields if field in row}


def _format_collection(
    name: str,
    rows: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    empty: str,
) -> list[str]:
    if not rows:
        return [f"{name}[0] -- {empty}"]
    lines = [f"{name}[{len(rows)}]"]
    for index, row in enumerate(rows):
        values = _project(row, fields)
        rendered = " ".join(f"{key}={_truncate(value)}" for key, value in values.items())
        lines.append(f"{name}[{index}] {rendered}".rstrip())
    return lines


def _format_scalar(payload: dict[str, Any], *, fields: tuple[str, ...] | None = None) -> list[str]:
    visible = fields or tuple(key for key in payload.keys() if key != "help")
    lines = [f"{key}: {_truncate(payload.get(key))}" for key in visible if key in payload]
    return lines or ["ok: true"]


def _format_help(lines: list[str] | tuple[str, ...] | None) -> list[str]:
    return [f"help[{index}] {line}" for index, line in enumerate(lines or [])]


def _format_toon(payload: dict[str, Any], *, fields: tuple[str, ...] | None = None) -> str:
    lines: list[str] = []
    if "jobs" in payload:
        lines.extend(_format_collection(
            "jobs",
            list(payload.get("jobs") or []),
            fields=fields or DEFAULT_JOB_FIELDS,
            empty="none pending",
        ))
    if "gates" in payload:
        lines.extend(_format_collection(
            "gates",
            list(payload.get("gates") or []),
            fields=fields or DEFAULT_GATE_FIELDS,
            empty="none active",
        ))
    if "events" in payload:
        lines.extend(_format_collection(
            "events",
            list(payload.get("events") or []),
            fields=fields or DEFAULT_EVENT_FIELDS,
            empty="none",
        ))
    if "lessons" in payload:
        lines.extend(_format_collection(
            "lessons",
            list(payload.get("lessons") or []),
            fields=fields or DEFAULT_LESSON_FIELDS,
            empty="none",
        ))
    if "trends" in payload:
        lines.extend(_format_collection(
            "trends",
            list(payload.get("trends") or []),
            fields=fields or DEFAULT_TREND_FIELDS,
            empty="none",
        ))
    scalar_keys = {
        key: value
        for key, value in payload.items()
        if key not in {"jobs", "gates", "events", "lessons", "trends", "help"}
    }
    if scalar_keys:
        lines.extend(_format_scalar(scalar_keys, fields=fields))
    lines.extend(_format_help(payload.get("help")))
    return "\n".join(lines) + "\n"


def _emit(payload: dict[str, Any], *, json_output: bool, fields: tuple[str, ...] | None) -> None:
    safe = redact(payload)
    if json_output:
        print(json.dumps(safe, indent=2, sort_keys=True))
    else:
        sys.stdout.write(_format_toon(safe, fields=fields))


def _api(cfg: Config, state: State) -> CodexSupervisorMcpAPI:
    return CodexSupervisorMcpAPI(cfg, state)


def _home(state: State) -> dict[str, Any]:
    jobs = state.list_dual_agent_workflow_jobs(active_only=True, limit=10)
    gates = state.list_active_dual_agent_workflow_steps(limit=10)
    return {
        "status": "ok",
        "totalCount": {"jobs": len(jobs), "gates": len(gates)},
        "jobs": jobs,
        "gates": gates,
        "help": [
            "Run `codex-supervisor-axi submit --task-id <id> --run-id <id> --intent <text>`.",
            "Run `codex-supervisor-axi poll <job_id>` for a durable job status.",
            "Start the detached dispatcher with `codex-supervisor-workflow-dispatcher`.",
        ],
    }


def _submit(args: argparse.Namespace, cfg: Config, state: State) -> dict[str, Any]:
    tool_receipts = _read_json_arg(args.tool_receipts_json, default=[])
    planning_artifacts = _read_json_arg(args.planning_artifacts_json, default=[])
    result = _api(cfg, state).submit_dual_agent_workflow_job(
        cwd=args.cwd,
        task_id=args.task_id,
        run_id=args.run_id,
        intent=args.intent,
        quality=args.quality,
        execution_layer_mode=args.execution_layer_mode,
        agentic_lead_policy=args.agentic_lead_policy,
        require_skill_receipts=args.require_skill_receipts,
        cursor_review=args.cursor_review,
        cursor_review_profile=args.cursor_review_profile,
        reviewer_output_mode=args.reviewer_output_mode,
        reviewer_unavailable_policy=args.reviewer_unavailable_policy,
        planning_artifacts=planning_artifacts,
        tool_receipts=tool_receipts,
        config_path=args.config,
        client_token=args.client_token,
    )
    result["help"] = [
        f"Run `codex-supervisor-axi poll {result['job_id']}`.",
        "If the transport closes, rerun submit with the same --client-token or run catch-up.",
    ]
    return result


def _poll(args: argparse.Namespace, cfg: Config, state: State) -> dict[str, Any]:
    result = _api(cfg, state).poll_dual_agent_workflow_job(job_id=args.job_id)
    result["help"] = [
        f"Run `codex-supervisor-axi catch-up {result.get('run_id', '<run_id>')}` for the event tail.",
        "Ensure `codex-supervisor-workflow-dispatcher` is running if the job stays reserved.",
    ]
    return result


def _catch_up(args: argparse.Namespace, cfg: Config, state: State) -> dict[str, Any]:
    result = _api(cfg, state).catch_up_dual_agent_workflow(
        run_id=args.run_id,
        last_event_id=args.last_event_id,
        limit=args.limit,
    )
    result["help"] = [
        f"Run `codex-supervisor-axi catch-up {args.run_id} --last-event-id {result['next_event_id']}`.",
    ]
    return result


def _gates(args: argparse.Namespace, _cfg: Config, state: State) -> dict[str, Any]:
    rows = (
        [
            dict(row)
            for row in state.list_dual_agent_workflow_steps(
                run_id=args.run_id,
                task_id=args.task_id,
            )
        ]
        if args.task_id
        else [
            {
                "event_id": row["event_id"],
                "ts": row["ts"],
                "kind": row["kind"],
                **json.loads(row["payload_json"]),
            }
            for row in state.read_dual_agent_gate_events(args.run_id)
        ]
    )
    return {
        "status": "ok",
        "run_id": args.run_id,
        "gates": rows,
        "help": ["Run `codex-supervisor-axi status <job_id>` for the durable job state."],
    }


def _operator_decision(args: argparse.Namespace, _cfg: Config, state: State, *, decision: str) -> dict[str, Any]:
    event_id = state.write_event(
        run_id=args.run_id,
        source="operator",
        kind="supervisor_axi_operator_decision",
        payload={
            "decision": decision,
            "reason": args.reason,
            "subject": args.subject,
            "approval_channel": args.approval_channel,
        },
    )
    return {
        "status": "ok",
        "decision": decision,
        "event_id": event_id,
        "run_id": args.run_id,
        "help": [f"Run `codex-supervisor-axi catch-up {args.run_id} --last-event-id {event_id - 1}`."],
    }


def _lessons(args: argparse.Namespace, _cfg: Config, state: State) -> dict[str, Any]:
    return {
        "status": "ok",
        "lessons": state.list_supervisor_lessons(limit=args.limit),
        "help": ["Use --fields task_class,gate,taxonomy_code,remediation for more detail."],
    }


def _trends(args: argparse.Namespace, _cfg: Config, state: State) -> dict[str, Any]:
    return {
        "status": "ok",
        "trends": state.query_quality_trends(task_class=args.task_class, gate=args.gate),
        "help": ["Use --json for exact metric fields."],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = AxiArgumentParser(
        prog="codex-supervisor-axi",
        description="Non-blocking CLI for supervisor workflow jobs.",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--fields", help="Comma-separated output fields for TOON output.")
    subparsers = parser.add_subparsers(dest="command", parser_class=AxiArgumentParser)

    submit = subparsers.add_parser("submit")
    submit.add_argument("--cwd", default=os.getcwd())
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--run-id", required=True)
    submit.add_argument("--intent", required=True)
    submit.add_argument("--client-token")
    submit.add_argument("--quality", default="best")
    submit.add_argument("--execution-layer-mode", default="lead_direct")
    submit.add_argument("--agentic-lead-policy", default="off")
    submit.add_argument("--require-skill-receipts", action=argparse.BooleanOptionalAction, default=True)
    submit.add_argument("--cursor-review", action=argparse.BooleanOptionalAction, default=True)
    submit.add_argument("--cursor-review-profile", default="rigorous")
    submit.add_argument("--reviewer-output-mode", default="cursor_sdk")
    submit.add_argument("--reviewer-unavailable-policy", default="block")
    submit.add_argument("--tool-receipts-json")
    submit.add_argument("--planning-artifacts-json")

    poll = subparsers.add_parser("poll")
    poll.add_argument("job_id")
    status = subparsers.add_parser("status")
    status.add_argument("job_id")

    catch_up = subparsers.add_parser("catch-up")
    catch_up.add_argument("run_id")
    catch_up.add_argument("--last-event-id", type=int, default=0)
    catch_up.add_argument("--limit", type=int, default=100)

    gates = subparsers.add_parser("gates")
    gates.add_argument("run_id")
    gates.add_argument("--task-id")

    for name in ("approve", "deny"):
        decision = subparsers.add_parser(name)
        decision.add_argument("--run-id", required=True)
        decision.add_argument("--subject", default="operator_decision")
        decision.add_argument("--reason", default="")
        decision.add_argument("--approval-channel", default="cli")

    lessons = subparsers.add_parser("lessons")
    lessons.add_argument("--limit", type=int, default=20)

    trends = subparsers.add_parser("trends")
    trends.add_argument("--task-class")
    trends.add_argument("--gate")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        fields = _split_fields(args.fields)
        cfg = Config.load(args.config)
        state = State(cfg.supervisor.state_db)
        if args.command is None:
            payload = _home(state)
        elif args.command == "submit":
            payload = _submit(args, cfg, state)
        elif args.command in {"poll", "status"}:
            payload = _poll(args, cfg, state)
        elif args.command == "catch-up":
            payload = _catch_up(args, cfg, state)
        elif args.command == "gates":
            payload = _gates(args, cfg, state)
        elif args.command == "approve":
            payload = _operator_decision(args, cfg, state, decision="approve")
        elif args.command == "deny":
            payload = _operator_decision(args, cfg, state, decision="deny")
        elif args.command == "lessons":
            payload = _lessons(args, cfg, state)
        elif args.command == "trends":
            payload = _trends(args, cfg, state)
        else:
            raise ValueError(f"unknown command: {args.command}")
        _emit(payload, json_output=args.json_output, fields=fields)
        return 0
    except Exception as e:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        fields = None
        if "--fields" in raw_argv:
            field_index = raw_argv.index("--fields") + 1
            fields = _split_fields(raw_argv[field_index]) if field_index < len(raw_argv) else None
        json_output = "--json" in raw_argv
        payload = {
            "error": {
                "code": e.__class__.__name__,
                "message": str(e),
            },
            "help": ["Run `codex-supervisor-axi --help` for command syntax."],
        }
        _emit(payload, json_output=json_output, fields=fields)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
