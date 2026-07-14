#!/usr/bin/env python3
"""Pinned Claude Code runtime adapter for SWE-bench Pro attempts.

The parent solver invokes this script inside one isolated public worktree per
attempt. This script renders the public packet into a pinned prompt, executes it
through the provider-neutral ``AgentRuntime`` lifecycle, and writes the attempt
metadata JSON expected by ``supervisor.swe_bench_solver``. Diff capture stays in
the parent solver so this wrapper does not inspect or serialize patches.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from supervisor.agent_runtime import AgentTask, ClaudeCodeRuntime
from supervisor.runtime_execution import execute_agent_task_blocking


PUBLIC_PACKET_ENV = "SWEBENCH_SOLVER_PUBLIC_PACKET"
ATTEMPT_OUTPUT_ENV = "SWEBENCH_SOLVER_ATTEMPT_OUTPUT"
DECISION_RE = re.compile(r"SWEBENCH_SOLVER_DECISION:\s*(accept|reject)\b", re.I)


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def render_prompt(template: str, packet: Mapping[str, Any]) -> str:
    """Render the prompt template using explicit SWE-bench placeholders."""
    replacements = {
        "instance_id": str(packet.get("instance_id") or ""),
        "repo": str(packet.get("repo") or ""),
        "base_commit": str(packet.get("base_commit") or ""),
        "problem_statement": str(packet.get("problem_statement") or ""),
        "attempt_index": str(packet.get("attempt_index") or ""),
        "attempt_count": str(packet.get("attempt_count") or ""),
        "public_packet_json": _canonical_json(dict(packet)),
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _direct_claude_env(api_key_env: str) -> dict[str, str]:
    env = dict(os.environ)
    api_key = str(env.get(api_key_env) or "").strip()
    if not api_key:
        raise ValueError(
            f"refusing to run Claude Code without direct Anthropic credential "
            f"{api_key_env}"
        )
    env["ANTHROPIC_API_KEY"] = api_key
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    return env


def _normalise_usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    result: dict[str, Any] = dict(usage) if isinstance(usage, Mapping) else {}
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, Mapping):
        for item in model_usage.values():
            if isinstance(item, Mapping):
                if "contextWindow" in item:
                    result.setdefault("context_window", item["contextWindow"])
                if "maxOutputTokens" in item:
                    result.setdefault("max_output_tokens", item["maxOutputTokens"])
                break
    return result


def _decision_from_result(result_text: str) -> bool:
    matches = DECISION_RE.findall(result_text)
    if not matches:
        raise ValueError(
            "Claude result missing final SWEBENCH_SOLVER_DECISION: accept|reject marker"
        )
    return matches[-1].lower() == "accept"


def _build_attempt_output(
    *,
    packet: Mapping[str, Any],
    claude_payload: Mapping[str, Any],
    prompt_sha256: str,
    accept: bool,
    model: str,
    provider: str,
    runner_label: str,
    credential_env: str,
    claude_version: str,
) -> dict[str, Any]:
    instance_id = str(packet.get("instance_id") or "instance")
    attempt_index = str(packet.get("attempt_index") or "attempt")
    candidate_id = f"{instance_id}-attempt-{attempt_index}"
    return {
        "schema_version": "supervisor-swebench-claude-code-attempt-output/v1",
        "candidate_id": candidate_id,
        "accept": accept,
        "model": str(claude_payload.get("model") or model),
        "provider": provider,
        "runner_label": runner_label,
        "prompt_sha256": prompt_sha256,
        "cost_usd": float(claude_payload.get("total_cost_usd") or 0.0),
        "token_usage": _normalise_usage(claude_payload),
        "route": {
            "kind": "anthropic_direct",
            "credential_env": credential_env,
            "proxy_fields_removed": [
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
            ],
        },
        "claude_code": {
            "version": claude_version,
            "output_format": "json",
            "mode": "bare_print",
        },
    }


def _claude_version(claude_bin: str) -> str:
    result = subprocess.run(
        [claude_bin, "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    return (result.stdout or result.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-template", required=True)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--model", default=os.environ.get("SWEBENCH_SOLVER_MODEL", ""))
    parser.add_argument(
        "--provider",
        default=os.environ.get("SWEBENCH_SOLVER_PROVIDER", "anthropic_direct"),
    )
    parser.add_argument(
        "--runner-label",
        default=os.environ.get("SWEBENCH_SOLVER_SOLVER", "claude-code-direct-haiku"),
    )
    parser.add_argument(
        "--anthropic-api-key-env",
        default="ANTHROPIC_API_KEY",
    )
    parser.add_argument("--max-budget-usd", type=float, default=0.2)
    parser.add_argument("--permission-mode", default="bypassPermissions")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--claude-extra-arg", action="append", default=[])
    args = parser.parse_args(argv)

    input_path = Path(os.environ[PUBLIC_PACKET_ENV])
    output_path = Path(os.environ[ATTEMPT_OUTPUT_ENV])
    packet = _read_json(input_path)
    template = Path(args.prompt_template).read_text(encoding="utf-8")
    prompt = render_prompt(template, packet)
    prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()
    env = _direct_claude_env(args.anthropic_api_key_env)
    execution = execute_agent_task_blocking(
        ClaudeCodeRuntime(binary=args.claude_bin),
        AgentTask(
            task_id=(
                "swebench-pro-"
                f"{packet.get('instance_id') or 'instance'}-"
                f"{packet.get('attempt_index') or 'attempt'}"
            ),
            instruction=prompt,
            cwd=Path.cwd(),
            model=args.model,
            timeout_s=max(0.001, float(args.timeout_s)),
            env=env,
            inherit_env=False,
            metadata={
                "bare": True,
                "no_session_persistence": True,
                "tools": "default",
                "permission_mode": args.permission_mode,
                "max_budget_usd": float(args.max_budget_usd),
                "extra_args": tuple(args.claude_extra_arg),
                "result_metadata": {
                    "runner_label": args.runner_label,
                    "route_kind": "anthropic_direct",
                },
            },
        ),
    )
    result = execution.result
    if result.status != "completed":
        sys.stderr.write(
            str(result.metadata.get("stderr") or result.output or result.status)
        )
        return 1
    result_text = result.output
    accept = _decision_from_result(result_text)
    output = _build_attempt_output(
        packet=packet,
        claude_payload={
            "model": result.resolved_model or args.model,
            "total_cost_usd": result.cost_usd,
            "usage": dict(result.token_usage),
        },
        prompt_sha256=prompt_hash,
        accept=accept,
        model=args.model,
        provider=args.provider,
        runner_label=args.runner_label,
        credential_env=args.anthropic_api_key_env,
        claude_version=_claude_version(args.claude_bin),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
