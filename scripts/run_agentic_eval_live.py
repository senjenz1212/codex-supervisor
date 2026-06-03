#!/usr/bin/env python3
"""Record real three-arm agentic eval data from the curated labeled set."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from supervisor.agentic_eval import agentic_eval_runner
from supervisor.agentic_eval_assembler import (
    CliDualAgentWorkflowRunner,
    assemble_agentic_eval_dataset,
    select_labeled_cases,
    write_agentic_eval_dataset,
)
from supervisor.agentic_eval_corpus import load_agentic_eval_labeled_set


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labeled-set", default="tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir", default="docs/dual-agent/agentic-eval-bridge-20260603/agentic-eval-live")
    parser.add_argument("--task-id", action="append", dest="task_ids", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--min-subagents", type=int, default=1)
    parser.add_argument("--quality", default="best")
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--allow-live-calls", action="store_true")
    args = parser.parse_args(argv)

    if not args.allow_live_calls:
        print("refusing live workflow execution without --allow-live-calls", file=sys.stderr)
        return 2

    repo_root = Path(args.repo_root).expanduser().resolve()
    output_dir = Path(args.output_dir)
    cassettes_dir = output_dir / "cassettes"
    dataset_path = output_dir / "agentic_eval_three_arm_dataset.yaml"
    report_dir = output_dir / "report"

    labeled_set = load_agentic_eval_labeled_set(args.labeled_set, repo_root=repo_root)
    cases = select_labeled_cases(
        labeled_set,
        task_ids=args.task_ids or None,
        limit=None if args.task_ids else args.limit,
    )
    runner = CliDualAgentWorkflowRunner(
        repo_root=repo_root,
        output_dir=output_dir,
        quality=args.quality,
        timeout_s=args.timeout_s,
    )
    dataset = assemble_agentic_eval_dataset(
        cases=cases,
        workflow_runner=runner,
        cassette_dir=cassettes_dir,
        repo_root=repo_root,
        run_id_prefix=f"agentic-eval-bridge-20260603-{uuid.uuid4().hex[:8]}",
        min_subagents=args.min_subagents,
    )
    write_agentic_eval_dataset(dataset, dataset_path)
    report = agentic_eval_runner(dataset_path=dataset_path, output_dir=report_dir)
    summary = {
        "status": "recorded",
        "task_count": len(cases),
        "dataset": str(dataset_path),
        "report": report["exports"]["report_json"],
        "report_sha256": report["report_sha256"],
        "default_change_allowed": report["default_change_allowed"],
        "agentic_lead_policy": report["agentic_lead_policy_snapshot"]["policy"],
        "summary": report["summary"],
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
