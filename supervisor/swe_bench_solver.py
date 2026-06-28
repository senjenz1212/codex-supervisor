"""SWE-bench Pro solver adapter primitives for report-only pilots."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence


GENERATOR_INPUT_ENV = "SWEBENCH_MERGEABILITY_GENERATOR_INPUT"
GENERATOR_OUTPUT_ENV = "SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT"
SOLVER_PUBLIC_PACKET_ENV = "SWEBENCH_SOLVER_PUBLIC_PACKET"
ATTEMPT_OUTPUT_ENV = "SWEBENCH_SOLVER_ATTEMPT_OUTPUT"

_FORBIDDEN_PUBLIC_PACKET_KEYS = frozenset({
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "fail_to_pass",
    "pass_to_pass",
    "test_patch",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
    "expected_outcome",
    "final_score",
    "oracle_accept",
    "hidden_test_commands",
})
_FORBIDDEN_PUBLIC_PACKET_TEXT = (
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "fail_to_pass",
    "pass_to_pass",
    "test_patch",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
    "hidden/test_behavior.py",
    ".mergeability/",
)


@dataclass(frozen=True)
class SweBenchInstance:
    """Minimal instance metadata needed by the solver adapters."""

    instance_id: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""


def capture_model_patch(repo_path: str | Path) -> str:
    """Return the current git diff as a SWE-bench ``model_patch`` string."""
    repo = Path(repo_path)
    if not repo.exists():
        raise FileNotFoundError(repo)
    tracked = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    return tracked


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _forbidden_public_refs(value: Any, *, path: str = "$") -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key in _FORBIDDEN_PUBLIC_PACKET_KEYS:
                refs.append(child_path)
            refs.extend(_forbidden_public_refs(item, path=child_path))
        return refs
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            refs.extend(_forbidden_public_refs(item, path=f"{path}[{index}]"))
        return refs
    if isinstance(value, str):
        for marker in _FORBIDDEN_PUBLIC_PACKET_TEXT:
            if marker in value:
                refs.append(f"{path}:{marker}")
    return refs


def _validate_public_generator_input(payload: Mapping[str, Any]) -> None:
    refs = _forbidden_public_refs(payload)
    if refs:
        raise ValueError(
            "forbidden oracle material in SWE-bench generator input: "
            + ", ".join(refs)
        )
    schema_version = str(payload.get("schema_version") or "")
    if schema_version != "supervisor-swebench-mergeability-live-generator-input/v1":
        raise ValueError(
            "unsupported SWE-bench generator input schema_version: "
            f"{schema_version!r}"
        )
    required = ("instance_id", "problem_statement", "public_worktree_ref")
    missing = [name for name in required if not str(payload.get(name) or "").strip()]
    if missing:
        raise ValueError("SWE-bench generator input missing: " + ", ".join(missing))


def _run_git(repo: Path, args: Sequence[str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _copy_public_worktree(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if (source / ".git").exists():
        raise ValueError("forbidden .git metadata in public_worktree_ref")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    _run_git(destination, ["init"])
    _run_git(destination, ["config", "user.email", "swebench-solver@example.invalid"])
    _run_git(destination, ["config", "user.name", "SWE Bench Solver"])
    _run_git(destination, ["add", "-A"])
    _run_git(destination, ["commit", "--allow-empty", "-m", "public base"])


def _read_attempt_output(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"runner did not write attempt output JSON: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("runner attempt output must be a JSON object")
    return dict(raw)


def _runner_public_packet(
    payload: Mapping[str, Any],
    *,
    attempt_index: int,
    attempt_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-swebench-single-agent-public-packet/v1",
        "instance_id": str(payload.get("instance_id") or ""),
        "repo": str(payload.get("repo") or ""),
        "base_commit": str(payload.get("base_commit") or ""),
        "problem_statement": str(payload.get("problem_statement") or ""),
        "public_checkout_ref": str(payload.get("public_checkout_ref") or ""),
        "public_checkout_sha256": str(payload.get("public_checkout_sha256") or ""),
        "public_worktree_sha256": str(payload.get("public_worktree_sha256") or ""),
        "public_worktree_manifest": list(payload.get("public_worktree_manifest") or []),
        "generation_config": dict(payload.get("generation_config") or {}),
        "prompt_hash": str(payload.get("prompt_hash") or ""),
        "generator_input_hash": str(payload.get("generator_input_hash") or ""),
        "attempt_index": attempt_index,
        "attempt_count": attempt_count,
    }


def _attempt_record(
    *,
    generator_input: Mapping[str, Any],
    attempt_index: int,
    attempt_count: int,
    solver: str,
    patch: str,
    runner_output: Mapping[str, Any],
    wall_clock_s: float,
) -> dict[str, Any]:
    generation_config = (
        dict(generator_input.get("generation_config"))
        if isinstance(generator_input.get("generation_config"), Mapping)
        else {}
    )
    instance_id = str(generator_input.get("instance_id") or "")
    model = str(generation_config.get("model") or runner_output.get("model") or "")
    provider = str(
        generation_config.get("provider") or runner_output.get("provider") or ""
    )
    runner_label = str(solver or runner_output.get("runner_label") or "").strip()
    if not model:
        raise ValueError("generation_config.model is required for baseline receipt")
    if not provider:
        raise ValueError("generation_config.provider is required for baseline receipt")
    if not runner_label:
        raise ValueError("--solver is required for baseline receipt")
    patch_hash = sha256(patch.encode("utf-8")).hexdigest()
    candidate_id = str(
        runner_output.get("candidate_id")
        or f"{instance_id}-attempt-{attempt_index}"
    )
    prompt_sha256 = str(
        runner_output.get("prompt_sha256")
        or generator_input.get("prompt_hash")
        or _sha256_json(_runner_public_packet(
            generator_input,
            attempt_index=attempt_index,
            attempt_count=attempt_count,
        ))
    )
    producer = {
        "model": model,
        "provider": provider,
        "runner_label": runner_label,
    }
    receipt = {
        "candidate_id": candidate_id,
        "accept": bool(runner_output.get("accept")),
        "decision_source": "single_agent_candidate_generation",
        "candidate_artifact_hash": patch_hash,
        "producer": producer,
        "prompt_sha256": prompt_sha256,
    }
    token_usage = runner_output.get("token_usage")
    if not isinstance(token_usage, Mapping):
        token_usage = {}
    return {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "attempt_index": attempt_index,
        "attempt_count": attempt_count,
        "model_patch": patch,
        "candidate_artifact_hash": patch_hash,
        "model_patch_sha256": patch_hash,
        "diff_sha256": patch_hash,
        "accept": bool(runner_output.get("accept")),
        "cost_usd": float(runner_output.get("cost_usd") or 0.0),
        "wall_clock_s": float(wall_clock_s),
        "token_usage": dict(token_usage),
        "origin": {
            "kind": "single_agent_solver_attempt",
            "attempt_index": attempt_index,
            "attempt_count": attempt_count,
            "generator_input_hash": str(generator_input.get("generator_input_hash") or ""),
            "public_worktree_sha256": str(generator_input.get("public_worktree_sha256") or ""),
        },
        "producer": dict(producer),
        "single_agent_baseline_decision": receipt,
    }


def _run_generator_mode(args: argparse.Namespace, *, input_path: Path, output_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("SWE-bench generator input must be a JSON object")
    _validate_public_generator_input(payload)
    if args.k <= 1:
        raise ValueError("--k must be greater than 1 for corpus-producing runs")
    if not str(args.runner_command or "").strip():
        raise ValueError("--runner-command is required in generator mode")
    public_worktree = Path(str(payload["public_worktree_ref"])).expanduser().resolve()
    runner_argv = shlex.split(str(args.runner_command))
    if not runner_argv:
        raise ValueError("--runner-command is empty")

    output_root = output_path.parent / "solver-attempts"
    output_root.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    total_cost = 0.0
    for attempt_index in range(1, args.k + 1):
        attempt_dir = output_root / f"attempt-{attempt_index}"
        attempt_io_dir = output_root / f"attempt-{attempt_index}-io"
        attempt_io_dir.mkdir(parents=True, exist_ok=True)
        _copy_public_worktree(public_worktree, attempt_dir)
        attempt_public_packet = _runner_public_packet(
            payload,
            attempt_index=attempt_index,
            attempt_count=args.k,
        )
        public_packet_path = attempt_io_dir / "public_packet.json"
        public_packet_path.write_text(
            json.dumps(attempt_public_packet, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        attempt_output_path = attempt_io_dir / "attempt_output.json"
        env = os.environ.copy()
        env.update({
            "SWEBENCH_SOLVER_ATTEMPT_INDEX": str(attempt_index),
            "SWEBENCH_SOLVER_SOLVER": str(args.solver),
            "SWEBENCH_SOLVER_MODEL": str(
                (payload.get("generation_config") or {}).get("model")
                if isinstance(payload.get("generation_config"), Mapping)
                else ""
            ),
            "SWEBENCH_SOLVER_PROVIDER": str(
                (payload.get("generation_config") or {}).get("provider")
                if isinstance(payload.get("generation_config"), Mapping)
                else ""
            ),
            SOLVER_PUBLIC_PACKET_ENV: str(public_packet_path),
            ATTEMPT_OUTPUT_ENV: str(attempt_output_path),
        })
        started = time.monotonic()
        result = subprocess.run(
            runner_argv,
            cwd=attempt_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        wall_clock_s = time.monotonic() - started
        if result.returncode != 0:
            raise RuntimeError(
                f"runner command failed on attempt {attempt_index} with "
                f"{result.returncode}: {result.stderr or result.stdout}"
            )
        runner_output = _read_attempt_output(attempt_output_path)
        _run_git(attempt_dir, ["add", "-N", "--", "."])
        patch = capture_model_patch(attempt_dir)
        if not patch.strip():
            raise ValueError(f"attempt {attempt_index} produced an empty model_patch")
        attempt = _attempt_record(
            generator_input=payload,
            attempt_index=attempt_index,
            attempt_count=args.k,
            solver=args.solver,
            patch=patch,
            runner_output=runner_output,
            wall_clock_s=wall_clock_s,
        )
        total_cost += float(attempt["cost_usd"])
        if total_cost > args.max_budget_usd:
            raise RuntimeError(
                "single-agent solver budget exceeded: "
                f"{total_cost:.6f} > {args.max_budget_usd:.6f}"
            )
        attempts.append(attempt)

    first = attempts[0]
    output = {
        "schema_version": "supervisor-swebench-single-agent-generator-output/v1",
        "instance_id": str(payload.get("instance_id") or ""),
        "candidate_id": first["candidate_id"],
        "model_patch": first["model_patch"],
        "candidate_artifact_hash": first["candidate_artifact_hash"],
        "model_patch_sha256": first["model_patch_sha256"],
        "diff_sha256": first["diff_sha256"],
        "accept": first["accept"],
        "cost_usd": round(total_cost, 6),
        "token_usage": first["token_usage"],
        "origin": first["origin"],
        "producer": first["producer"],
        "single_agent_baseline_decision": first["single_agent_baseline_decision"],
        "attempts": attempts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def solver_attempts_for_candidate_corpus(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every generated attempt in the JSONL shape expected by the corpus builder."""
    raw_attempts = payload.get("attempts")
    if isinstance(raw_attempts, Sequence) and not isinstance(raw_attempts, (str, bytes, bytearray)):
        source_attempts = raw_attempts
    else:
        source_attempts = [payload]
    attempts: list[dict[str, Any]] = []
    for index, raw in enumerate(source_attempts, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"solver attempt {index} must be a JSON object")
        attempt = dict(raw)
        if "instance_id" not in attempt and payload.get("instance_id"):
            attempt["instance_id"] = payload["instance_id"]
        if "candidate_id" not in attempt:
            attempt["candidate_id"] = f"{attempt.get('instance_id', 'candidate')}-attempt-{index}"
        attempts.append(attempt)
    return attempts


def write_model_patch_jsonl(
    path: str | Path,
    *,
    instance_id: str,
    model_patch: str,
) -> dict[str, str]:
    """Write one SWE-bench Pro prediction row with the required shape."""
    clean_instance_id = str(instance_id).strip()
    if not clean_instance_id:
        raise ValueError("instance_id is required")
    row = {
        "instance_id": clean_instance_id,
        "model_patch": str(model_patch or ""),
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    return row


def evaluator_patch_row(row: dict[str, Any], *, prefix: str) -> dict[str, str]:
    """Convert the solver row into the patch key used by SWE-bench Pro evaluators."""
    return {
        "instance_id": str(row.get("instance_id") or ""),
        "patch": str(row.get("model_patch") or ""),
        "prefix": str(prefix),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="ScaleAI/SWE-bench_Pro")
    parser.add_argument("--dataset-split", default="test")
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument("--solver", required=True)
    parser.add_argument("--lead-model-alias", default="")
    parser.add_argument("--underlying-lead-model", default="")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--max-budget-usd", type=float, default=0.0)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--output", default="")
    parser.add_argument("--runner-command", default="")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args(argv)

    if not args.allow_live:
        print(
            "refusing live SWE-bench Pro solver run without --allow-live; "
            "this adapter is safe-by-default",
            file=sys.stderr,
        )
        return 2
    if args.max_budget_usd <= 0:
        print(
            "refusing live SWE-bench Pro solver run without --max-budget-usd > 0",
            file=sys.stderr,
        )
        return 2
    generator_input = os.environ.get(GENERATOR_INPUT_ENV)
    generator_output = os.environ.get(GENERATOR_OUTPUT_ENV)
    if generator_input or generator_output:
        if not generator_input or not generator_output:
            print(
                "refusing SWE-bench generator mode without both "
                f"{GENERATOR_INPUT_ENV} and {GENERATOR_OUTPUT_ENV}",
                file=sys.stderr,
            )
            return 2
        try:
            return _run_generator_mode(
                args,
                input_path=Path(generator_input),
                output_path=Path(generator_output),
            )
        except Exception as exc:
            print(f"SWE-bench single-agent solver failed: {exc}", file=sys.stderr)
            return 1
    if not args.output:
        print("refusing non-generator solver run without --output", file=sys.stderr)
        return 2
    raise RuntimeError(
        "live SWE-bench Pro execution is intentionally not wired into the "
        "adapter default path; use the pilot runner in replay/report mode or "
        "connect an approved checkout/evaluator bridge before spending budget"
    )


if __name__ == "__main__":
    raise SystemExit(main())
