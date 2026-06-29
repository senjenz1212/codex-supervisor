#!/usr/bin/env python3
"""Pinned Claude Code runner for SWE-bench Pro single-agent attempts.

The parent solver invokes this script inside one isolated public worktree per
attempt. This script renders the public packet into a pinned prompt, runs Claude
Code through the configured LiteLLM route, and writes the attempt metadata JSON
expected by ``supervisor.swe_bench_solver``. Diff capture stays in the parent
solver so this wrapper does not need to inspect or serialize patches.
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
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


PUBLIC_PACKET_ENV = "SWEBENCH_SOLVER_PUBLIC_PACKET"
ATTEMPT_OUTPUT_ENV = "SWEBENCH_SOLVER_ATTEMPT_OUTPUT"
DEFAULT_LITELLM_BASE_URL = "https://uai-litellm.internal.unity.com"
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


def _route_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path


def _claude_env(base_url: str, token_env: str) -> dict[str, str]:
    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = base_url
    token = env.get(token_env, "")
    if token:
        env.setdefault("ANTHROPIC_AUTH_TOKEN", token)
        env.setdefault("ANTHROPIC_API_KEY", token)
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
    match = DECISION_RE.search(result_text)
    if not match:
        raise ValueError(
            "Claude result missing final SWEBENCH_SOLVER_DECISION: accept|reject marker"
        )
    return match.group(1).lower() == "accept"


def build_claude_command(
    *,
    claude_bin: str,
    model: str,
    max_budget_usd: float,
    permission_mode: str,
    prompt: str,
    extra_args: Sequence[str] = (),
) -> list[str]:
    return [
        claude_bin,
        "--bare",
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools",
        "default",
        "--permission-mode",
        permission_mode,
        "--model",
        model,
        "--max-budget-usd",
        f"{max_budget_usd:.6f}",
        *extra_args,
        prompt,
    ]


def _build_attempt_output(
    *,
    packet: Mapping[str, Any],
    claude_payload: Mapping[str, Any],
    prompt_sha256: str,
    accept: bool,
    model: str,
    provider: str,
    runner_label: str,
    route_base_url: str,
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
            "kind": "anthropic_compatible_litellm",
            "base_url": route_base_url,
            "host": _route_host(route_base_url),
            "secret_env": "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
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
        default=os.environ.get("SWEBENCH_SOLVER_PROVIDER", "anthropic_via_unity_litellm"),
    )
    parser.add_argument(
        "--runner-label",
        default=os.environ.get("SWEBENCH_SOLVER_SOLVER", "claude-code-litellm-haiku"),
    )
    parser.add_argument(
        "--litellm-base-url",
        default=os.environ.get("SWEBENCH_SOLVER_LITELLM_BASE_URL", DEFAULT_LITELLM_BASE_URL),
    )
    parser.add_argument(
        "--litellm-token-env",
        default="SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
    )
    parser.add_argument("--max-budget-usd", type=float, default=0.2)
    parser.add_argument("--permission-mode", default="bypassPermissions")
    parser.add_argument("--claude-extra-arg", action="append", default=[])
    args = parser.parse_args(argv)

    input_path = Path(os.environ[PUBLIC_PACKET_ENV])
    output_path = Path(os.environ[ATTEMPT_OUTPUT_ENV])
    packet = _read_json(input_path)
    template = Path(args.prompt_template).read_text(encoding="utf-8")
    prompt = render_prompt(template, packet)
    prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()
    command = build_claude_command(
        claude_bin=args.claude_bin,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        permission_mode=args.permission_mode,
        prompt=prompt,
        extra_args=args.claude_extra_arg,
    )
    env = _claude_env(args.litellm_base_url, args.litellm_token_env)
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
        return result.returncode
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Claude JSON output must be an object")
    result_text = str(payload.get("result") or "")
    accept = _decision_from_result(result_text)
    output = _build_attempt_output(
        packet=packet,
        claude_payload=payload,
        prompt_sha256=prompt_hash,
        accept=accept,
        model=args.model,
        provider=args.provider,
        runner_label=args.runner_label,
        route_base_url=args.litellm_base_url,
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
