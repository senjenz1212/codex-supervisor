"""CLI for deterministic SWE-bench mergeability replay bundles."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from .mergeability_bench import (
    ConfiguredReviewerPanelOptions,
    build_configured_reviewer_panel,
)
from .swe_bench_mergeability import (
    SwebenchMergeabilityFixtureRunnerError,
    swebench_mergeability_live_runner,
    swebench_mergeability_replay_runner,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=0.0)
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--baseline-generator-command", default="")
    parser.add_argument("--supervisor-generator-command", default="")
    parser.add_argument(
        "--reviewer-panel-mode",
        choices=("unavailable", "configured"),
        default="unavailable",
    )
    parser.add_argument("--reviewer-output-mode", default="cursor_sdk")
    parser.add_argument("--reviewer-model", default="")
    parser.add_argument("--codex-model", default="gpt-5.5")
    args = parser.parse_args(argv)

    reviewer_panel = None
    reviewer_panel_mode = "custom"
    if args.reviewer_panel_mode == "configured":
        reviewer_panel_mode = "configured"
        reviewer_panel = build_configured_reviewer_panel(
            ConfiguredReviewerPanelOptions(
                reviewer_output_mode=args.reviewer_output_mode,
                reviewer_model=args.reviewer_model or None,
                codex_model=args.codex_model,
                review_cwd=Path.cwd(),
            )
        )

    try:
        if args.run_live:
            if not args.allow_live:
                print(
                    "refusing live SWE-bench mergeability run without --allow-live",
                    file=sys.stderr,
                )
                return 2
            if args.max_budget_usd <= 0:
                print(
                    "refusing live SWE-bench mergeability run without --max-budget-usd > 0",
                    file=sys.stderr,
                )
                return 2
            if not args.baseline_generator_command or not args.supervisor_generator_command:
                print(
                    "refusing live SWE-bench mergeability run without generator commands",
                    file=sys.stderr,
                )
                return 2
            output_dir = Path(args.output_dir)
            report = swebench_mergeability_live_runner(
                manifest_path=args.manifest,
                output_dir=output_dir,
                baseline_generator=_command_generator(
                    args.baseline_generator_command,
                    output_dir=output_dir,
                    arm="baseline",
                ),
                supervisor_generator=_command_generator(
                    args.supervisor_generator_command,
                    output_dir=output_dir,
                    arm="supervisor",
                ),
                allow_live=args.allow_live,
                max_budget_usd=args.max_budget_usd,
                model=args.model,
                provider=args.provider,
                timeout_s=args.timeout_s,
                reviewer_panel=reviewer_panel,
                reviewer_panel_mode=reviewer_panel_mode,
            )
        else:
            report = swebench_mergeability_replay_runner(
                manifest_path=args.manifest,
                output_dir=args.output_dir,
                reviewer_panel=reviewer_panel,
                reviewer_panel_mode=reviewer_panel_mode,
                timeout_s=args.timeout_s,
            )
    except SwebenchMergeabilityFixtureRunnerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge = report.get("bridge_report") or {}
    summary = {
        "status": "reported" if report.get("status") != "unavailable" else "unavailable",
        "report": report.get("report_path") or str(Path(args.output_dir) / "live_report.json"),
        "report_sha256": report["report_sha256"],
        "manifest": report["manifest_path"],
        "instance_count": report["instance_count"],
        "candidate_count": report["candidate_count"],
        "allow_live": bool(report.get("allow_live")),
        "live_generation_used": bool(report.get("live_generation_used")),
        "reviewer_panel_mode": args.reviewer_panel_mode,
        "s_probe_execution_substrate": report.get("s_probe_execution_substrate"),
        "far_tar_frr": bridge.get("far_tar_frr"),
        "metric_applyable": bool(report.get("metric_applyable") or bridge.get("metric_applyable")),
        "improvement_claim_allowed": bool(
            report.get("improvement_claim_allowed")
            or bridge.get("improvement_claim_allowed")
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _command_generator(
    command: str,
    *,
    output_dir: Path,
    arm: str,
):
    argv = shlex.split(command)
    if not argv:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"{arm} generator command is empty"
        )

    def _run(generator_input: Mapping[str, Any]) -> Mapping[str, Any]:
        instance_id = str(generator_input.get("instance_id") or "instance")
        work_dir = output_dir / "generator-io" / arm / _safe_fragment(instance_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        input_path = work_dir / "input.json"
        output_path = work_dir / "output.json"
        input_path.write_text(
            json.dumps(dict(generator_input), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SWEBENCH_MERGEABILITY_GENERATOR_INPUT"] = str(input_path)
        env["SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT"] = str(output_path)
        env["SWEBENCH_MERGEABILITY_GENERATOR_ARM"] = arm
        started = time.monotonic()
        result = subprocess.run(
            argv,
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        wall_clock_s = time.monotonic() - started
        if result.returncode != 0:
            raise SwebenchMergeabilityFixtureRunnerError(
                f"{arm} generator command failed with {result.returncode}: "
                + (result.stderr or result.stdout)
            )
        if not output_path.exists():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"{arm} generator did not write output JSON"
            )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SwebenchMergeabilityFixtureRunnerError(
                f"{arm} generator output must be a JSON object"
            )
        payload.setdefault("wall_clock_s", wall_clock_s)
        return payload

    return _run


def _safe_fragment(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in str(value)
    ).strip("_") or "artifact"


if __name__ == "__main__":
    raise SystemExit(main())
