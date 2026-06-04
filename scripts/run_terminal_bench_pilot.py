#!/usr/bin/env python3
"""Plan or report the Terminal-Bench 2.1 GPT-5.5 pilot."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from supervisor.terminal_bench_eval import (
    build_terminal_bench_pilot_plan,
    build_terminal_bench_report,
    export_terminal_bench_report,
    load_terminal_bench_pilot_sample,
    load_terminal_bench_results,
    run_terminal_bench_pilot_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", default="tests/fixtures/terminal_bench/pilot_sample.yaml")
    parser.add_argument("--results", default="")
    parser.add_argument("--output-dir", default="docs/dual-agent/bench-terminalbench21-gpt55-20260603/terminal-bench-pilot")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=0.0)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--harbor-executable", default="harbor")
    args = parser.parse_args(argv)

    sample = load_terminal_bench_pilot_sample(args.sample)
    plan = build_terminal_bench_pilot_plan(
        sample=sample,
        output_dir=args.output_dir,
        allow_live=args.allow_live,
        max_budget_usd=args.max_budget_usd,
        harbor_executable=args.harbor_executable,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "pilot-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.run_live:
        if not args.allow_live:
            print("refusing live Terminal-Bench run without --allow-live", file=sys.stderr)
            return 2
        if args.max_budget_usd <= 0:
            print("refusing live Terminal-Bench run without --max-budget-usd > 0", file=sys.stderr)
            return 2
        run_terminal_bench_pilot_plan(plan)

    summary = {
        "status": "planned",
        "plan": str(plan_path),
        "dataset": plan["dataset"],
        "model": plan["model"],
        "task_count": len(plan["task_ids"]),
        "k": plan["k"],
        "planned_runs": plan["planned_runs"],
        "allow_live": plan["allow_live"],
        "max_budget_usd": plan["max_budget_usd"],
        "default_change_allowed": plan["report_only"]["default_change_allowed"],
    }

    if args.results:
        results = load_terminal_bench_results(args.results)
        report = build_terminal_bench_report(sample=sample, results=results)
        exports = export_terminal_bench_report(report, output_dir)
        summary.update({
            "status": "reported",
            "report": exports["report_json"],
            "report_sha256": report["report_sha256"],
            "delta": report["delta"],
            "noise_floor": report["noise_floor"],
        })

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
