"""CLI for deterministic SWE-bench mergeability replay bundles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mergeability_bench import (
    ConfiguredReviewerPanelOptions,
    build_configured_reviewer_panel,
)
from .swe_bench_mergeability import swebench_mergeability_replay_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout-s", type=float, default=30.0)
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

    report = swebench_mergeability_replay_runner(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        reviewer_panel=reviewer_panel,
        reviewer_panel_mode=reviewer_panel_mode,
        timeout_s=args.timeout_s,
    )
    bridge = report["bridge_report"]
    summary = {
        "status": "reported",
        "report": report["report_path"],
        "report_sha256": report["report_sha256"],
        "manifest": report["manifest_path"],
        "instance_count": report["instance_count"],
        "candidate_count": report["candidate_count"],
        "allow_live": False,
        "reviewer_panel_mode": args.reviewer_panel_mode,
        "s_probe_execution_substrate": report["s_probe_execution_substrate"],
        "far_tar_frr": bridge["far_tar_frr"],
        "metric_applyable": bridge["metric_applyable"],
        "improvement_claim_allowed": bridge["improvement_claim_allowed"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
