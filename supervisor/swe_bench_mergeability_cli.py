"""CLI for deterministic SWE-bench mergeability replay bundles.

Exposes the replay, live, official-replay, official-all-arms diagnostic, and
SWE-bench Pro powered factorial runners. ``--powered-factorial`` adapts an
oracle-labeled Pro predictions JSONL through
``swebench_mergeability_powered_factorial_runner`` and surfaces the
report-only ``evidence_conversion_power_contract`` with no authority flag
mutations; the all-arms diagnostic remains a separate report-only path.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .mergeability_bench import (
    SUPERVISOR_CONFIGURED_PANEL_LITELLM_MODEL_DEFAULT,
    SUPERVISOR_CONFIGURED_PANEL_LITELLM_PROVIDER_FAMILY_DEFAULT,
    ConfiguredReviewerPanelOptions,
    build_configured_reviewer_panel,
)
from .swe_bench_mergeability import (
    SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS,
    SwebenchMergeabilityFixtureRunnerError,
    swebench_mergeability_live_runner,
    swebench_mergeability_official_all_arms_diagnostic_runner,
    swebench_mergeability_official_replay_runner,
    swebench_mergeability_powered_factorial_runner,
    swebench_mergeability_replay_runner,
    write_swebench_official_all_arms_blocked_artifact,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--official-replay", action="store_true")
    parser.add_argument("--official-all-arms-diagnostic", action="store_true")
    parser.add_argument("--powered-factorial", action="store_true")
    parser.add_argument("--dataset", default="")
    parser.add_argument("--dataset-split", default="test")
    parser.add_argument("--predictions", default="")
    parser.add_argument("--min-good", type=int, default=30)
    parser.add_argument("--min-bad", type=int, default=30)
    parser.add_argument("--min-discordant", type=int, default=25)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--allow-dataset-fetch", action="store_true")
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
    parser.add_argument(
        "--litellm-model",
        default=SUPERVISOR_CONFIGURED_PANEL_LITELLM_MODEL_DEFAULT,
        help="Opt in to a LiteLLM reviewer by providing its model name.",
    )
    parser.add_argument(
        "--litellm-provider-family",
        default=SUPERVISOR_CONFIGURED_PANEL_LITELLM_PROVIDER_FAMILY_DEFAULT,
        help="Explicit provider family for the opt-in LiteLLM reviewer.",
    )
    parser.add_argument(
        "--panel-aggregation-mode",
        choices=("conservative", "geometric_median"),
        default="geometric_median",
        help="Configured reviewer panel aggregation mode.",
    )
    parser.add_argument(
        "--oracle-adapter",
        default="",
        help=(
            "Python import path 'module:attr' resolving to an oracle runner "
            "callable. Required for --official-replay."
        ),
    )
    parser.add_argument(
        "--oracle-adapter-kind",
        default="official_docker_or_equivalent",
        help=(
            "Adapter kind label recorded with oracle receipts. Use "
            "'official_equivalent' to enable smoke label validation."
        ),
    )
    parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help="Repeatable: select only these SWE-bench instance ids.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit selection to first N rows (sorted by instance_id).",
    )
    parser.add_argument(
        "--dataset-loader",
        default="",
        help="Optional 'module:attr' import for a custom dataset loader.",
    )
    parser.add_argument(
        "--repo-materializer",
        default="",
        help="Optional 'module:attr' import for a custom repo materializer.",
    )
    parser.add_argument(
        "--swe-bench-pro-scripts-dir",
        default="",
        help=(
            "Optional SWE-bench Pro run_scripts root. Equivalent to "
            "SWEBENCH_PRO_ORACLE_SCRIPTS_DIR for official Pro replay preflight."
        ),
    )
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
                litellm_model=args.litellm_model or None,
                litellm_provider_family=args.litellm_provider_family or None,
                panel_aggregation_mode=args.panel_aggregation_mode,
                review_cwd=Path.cwd(),
            )
        )

    try:
        if args.powered_factorial:
            if not args.predictions:
                print(
                    "refusing powered factorial run without --predictions",
                    file=sys.stderr,
                )
                return 2
            report = swebench_mergeability_powered_factorial_runner(
                predictions_path=args.predictions,
                output_dir=args.output_dir,
                min_good=args.min_good,
                min_bad=args.min_bad,
                min_discordant=args.min_discordant,
                alpha=args.alpha,
                timeout_s=args.timeout_s,
            )
            summary = {
                "status": "reported",
                "report": report.get("report_path")
                or str(Path(args.output_dir) / "powered_factorial_report.json"),
                "report_sha256": report["report_sha256"],
                "candidate_count": report["candidate_count"],
                "source_predictions_path": report["source_predictions_path"],
                "metric_applyable": bool(report.get("metric_applyable")),
                "powered_metric_applyable": bool(report.get("powered_metric_applyable")),
                "evidence_conversion_power_contract": report.get(
                    "evidence_conversion_power_contract"
                ),
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        if args.official_replay or args.official_all_arms_diagnostic:
            if not args.allow_dataset_fetch:
                print(
                    "refusing official SWE-bench replay without --allow-dataset-fetch",
                    file=sys.stderr,
                )
                if args.official_all_arms_diagnostic:
                    _write_aeb0_cli_blocked_artifact(
                        args,
                        ["missing_cli_prerequisite:allow_dataset_fetch"],
                    )
                return 2
            if not args.dataset or not args.predictions:
                print(
                    "refusing official SWE-bench replay without --dataset and --predictions",
                    file=sys.stderr,
                )
                if args.official_all_arms_diagnostic:
                    reasons = []
                    if not args.dataset:
                        reasons.append("missing_cli_prerequisite:dataset")
                    if not args.predictions:
                        reasons.append("missing_cli_prerequisite:predictions")
                    _write_aeb0_cli_blocked_artifact(args, reasons)
                return 2
            if not args.oracle_adapter:
                print(
                    "refusing official SWE-bench replay without --oracle-adapter",
                    file=sys.stderr,
                )
                if args.official_all_arms_diagnostic:
                    _write_aeb0_cli_blocked_artifact(
                        args,
                        ["missing_cli_prerequisite:oracle_adapter"],
                    )
                return 2
            if (
                args.oracle_adapter_kind
                not in SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS
            ):
                allowed = ", ".join(
                    sorted(SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS)
                )
                print(
                    "unsupported official SWE-bench oracle adapter kind "
                    f"{args.oracle_adapter_kind!r}; expected one of: {allowed}",
                    file=sys.stderr,
                )
                if args.official_all_arms_diagnostic:
                    _write_aeb0_cli_blocked_artifact(
                        args,
                        [f"unsupported_oracle_adapter_kind:{args.oracle_adapter_kind}"],
                    )
                return 2
            oracle_runner = _resolve_import_callable(args.oracle_adapter)
            dataset_loader = (
                _resolve_import_callable(args.dataset_loader)
                if args.dataset_loader
                else None
            )
            repo_materializer = (
                _resolve_import_callable(args.repo_materializer)
                if args.repo_materializer
                else None
            )
            instance_ids = list(args.instance_id) or None
            limit = args.limit if args.limit > 0 else None
            official_kwargs = {
                "dataset": args.dataset,
                "dataset_split": args.dataset_split,
                "predictions_path": args.predictions,
                "output_dir": args.output_dir,
                "allow_dataset_fetch": args.allow_dataset_fetch,
                "dataset_loader": dataset_loader,
                "repo_materializer": repo_materializer,
                "oracle_runner": oracle_runner,
                "oracle_adapter_kind": args.oracle_adapter_kind,
                "instance_ids": instance_ids,
                "limit": limit,
                "reviewer_panel": reviewer_panel,
                "reviewer_panel_mode": reviewer_panel_mode,
                "timeout_s": args.timeout_s,
                "swe_bench_pro_scripts_dir": args.swe_bench_pro_scripts_dir or None,
            }
            if args.official_all_arms_diagnostic:
                report = swebench_mergeability_official_all_arms_diagnostic_runner(
                    **official_kwargs
                )
            else:
                report = swebench_mergeability_official_replay_runner(
                    **official_kwargs
                )
        elif args.run_live:
            if not args.manifest:
                print(
                    "refusing live SWE-bench mergeability run without --manifest",
                    file=sys.stderr,
                )
                return 2
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
            if not args.manifest:
                print(
                    "refusing replay without --manifest",
                    file=sys.stderr,
                )
                return 2
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
    metrics_suppressed = bool(
        report.get("status") == "unavailable"
        or bridge.get("metrics_suppressed")
    )
    summary = {
        "status": "reported" if report.get("status") != "unavailable" else "unavailable",
        "report": report.get("report_path") or str(Path(args.output_dir) / "live_report.json"),
        "report_sha256": report["report_sha256"],
        "manifest": (
            report.get("manifest_path")
            or report.get("generated_replay_manifest_path")
            or ""
        ),
        "instance_count": report["instance_count"],
        "candidate_count": report["candidate_count"],
        "allow_live": bool(report.get("allow_live")),
        "live_generation_used": bool(report.get("live_generation_used")),
        "reviewer_panel_mode": args.reviewer_panel_mode,
        "s_probe_execution_substrate": report.get("s_probe_execution_substrate"),
        "far_tar_frr": None if metrics_suppressed else bridge.get("far_tar_frr"),
        "metrics_suppressed": metrics_suppressed,
        "metrics_unavailable_reasons": (
            report.get("metrics_unavailable_reasons")
            or bridge.get("metrics_unavailable_reasons")
            or []
        ),
        "diagnostic_ready_for_scale": bool(report.get("diagnostic_ready_for_scale")),
        "all_arms_populated": bool(report.get("all_arms_populated")),
        "baseline_available": report.get("baseline_available"),
        "s_probe_available": report.get("s_probe_available"),
        "s_full_available": report.get("s_full_available"),
        "oracle_ceiling_available": report.get("oracle_ceiling_available"),
        "matched_true_accept_status": report.get(
            "matched_true_accept_status",
            bridge.get("matched_true_accept_status"),
        ),
        "metric_applyable": bool(report.get("metric_applyable") or bridge.get("metric_applyable")),
        "improvement_claim_allowed": bool(
            report.get("improvement_claim_allowed")
            or bridge.get("improvement_claim_allowed")
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _write_aeb0_cli_blocked_artifact(
    args: argparse.Namespace,
    blocked_reasons: list[str],
) -> None:
    write_swebench_official_all_arms_blocked_artifact(
        output_dir=args.output_dir,
        blocked_reasons=blocked_reasons,
        dataset=args.dataset,
        dataset_split=args.dataset_split,
        predictions_path=args.predictions or None,
        oracle_adapter_kind=args.oracle_adapter_kind,
    )


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


def _resolve_import_callable(spec: str) -> Callable[..., Any]:
    if ":" not in spec:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"import spec {spec!r} must be 'module:attr'"
        )
    module_name, _, attr_name = spec.partition(":")
    if not module_name or not attr_name:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"import spec {spec!r} must be 'module:attr'"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"could not import module {module_name!r}: {exc}"
        ) from exc
    try:
        target = getattr(module, attr_name)
    except AttributeError as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"module {module_name!r} has no attribute {attr_name!r}"
        ) from exc
    if not callable(target):
        raise SwebenchMergeabilityFixtureRunnerError(
            f"resolved {spec!r} is not callable"
        )
    return target


def _safe_fragment(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in str(value)
    ).strip("_") or "artifact"


if __name__ == "__main__":
    raise SystemExit(main())
