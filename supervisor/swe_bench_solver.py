"""SWE-bench Pro solver adapter primitives for report-only pilots."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    parser.add_argument("--output", required=True)
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
        print("refusing live SWE-bench Pro solver run without --max-budget-usd > 0", file=sys.stderr)
        return 2
    raise RuntimeError(
        "live SWE-bench Pro execution is intentionally not wired into the "
        "adapter default path; use the pilot runner in replay/report mode or "
        "connect an approved checkout/evaluator bridge before spending budget"
    )


if __name__ == "__main__":
    raise SystemExit(main())
