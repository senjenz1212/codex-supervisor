#!/usr/bin/env python3
"""Repeat SWE-bench Pro oracle labels and keep only stable candidates.

This wrapper is deliberately report-only. Unit tests inject a fake oracle;
live Docker oracle work is refused unless the operator passes ``--allow-live``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.swebench_pro_batch_driver import _augment_attempt_with_oracle_context
from supervisor.swe_bench_official_oracle import run_swe_bench_pro_oracle


AUTHORITY_FLAGS = {
    "metric_applyable": False,
    "improvement_claim_allowed": False,
    "powered_improvement_claim_allowed": False,
    "human_mergeability_claim_allowed": False,
    "default_change_allowed": False,
    "policy_mutated": False,
    "gate_advanced": False,
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}"),
    re.compile(r"crsr_[A-Za-z0-9A-Za-z_-]{12,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(^|[_-])(api[_-]?key|auth[_-]?token|access[_-]?token|secret|password|private[_-]?key|token)($|[_-])",
    re.IGNORECASE,
)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _looks_like_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _safe_reason(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return "oracle_unavailable"
    if _looks_like_secret_value(value):
        return "redacted_oracle_unavailable_reason"
    safe = [
        ch.lower() if ch.isalnum() else "_"
        for ch in value
    ]
    compact = "_".join(part for part in "".join(safe).split("_") if part)
    return compact[:120] or "oracle_unavailable"


def _assert_no_secret_values(value: Any, *, path: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if _SENSITIVE_KEY_PATTERN.search(key_text) and nested not in (None, "", [], {}):
                raise RuntimeError(f"secret-like value in {nested_path}")
            _assert_no_secret_values(nested, path=nested_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _assert_no_secret_values(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _looks_like_secret_value(value):
        raise RuntimeError(f"secret-like value in {path}")


def _read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        raw_items = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        raw = json.loads(text)
        raw_items = raw if isinstance(raw, list) else raw.get("records", [])
    if not isinstance(raw_items, list):
        raise ValueError(f"{path} must contain a list or JSONL objects")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{path} item {index} must be an object")
        items.append(dict(item))
    return items


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} line {index} must be an object")
        rows.append(dict(raw))
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(dict(payload)), encoding="utf-8")


def _records_by_instance(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records, start=1):
        instance_id = str(record.get("instance_id") or "")
        if not instance_id:
            raise ValueError(f"record {index} missing instance_id")
        result[instance_id] = dict(record)
    return result


def candidate_key(candidate: Mapping[str, Any]) -> str:
    instance_id = str(candidate.get("instance_id") or "")
    candidate_id = str(candidate.get("candidate_id") or "")
    candidate_hash = str(candidate.get("candidate_artifact_hash") or "")
    if not candidate_hash:
        model_patch = str(candidate.get("model_patch") or "")
        if model_patch:
            candidate_hash = sha256(model_patch.encode("utf-8")).hexdigest()
    if not instance_id or not candidate_id or not candidate_hash:
        raise RuntimeError("candidate missing instance_id, candidate_id, or patch hash")
    return f"{instance_id}/{candidate_id}/{candidate_hash}"


def _patch_applied_from_result(result: Mapping[str, Any]) -> bool | None:
    raw = result.get("patch_applied")
    if raw is None:
        receipt = result.get("oracle_adapter_receipt")
        if isinstance(receipt, Mapping):
            raw = receipt.get("patch_applied")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "passed"}:
            return True
        if normalized in {"false", "0", "no", "failed"}:
            return False
    return None


def _status_value(result: Mapping[str, Any], key: str) -> str:
    raw = result.get(key)
    if raw is None:
        receipt = result.get("oracle_adapter_receipt")
        if isinstance(receipt, Mapping):
            raw = receipt.get(key)
    return str(raw or "").strip().lower()


def _unavailable_reason(
    result: Mapping[str, Any],
    *,
    fail_to_pass_status: str,
    pass_to_pass_status: str,
    patch_applied: bool | None,
) -> str:
    reason = str(
        result.get("oracle_unavailable_reason")
        or result.get("unavailable_reason")
        or ""
    ).strip()
    if reason:
        return _safe_reason(reason)
    if patch_applied is False:
        return "patch_apply_failed"
    if patch_applied is None:
        return "patch_applied_evidence_missing_or_false"
    if fail_to_pass_status == "unavailable" or pass_to_pass_status == "unavailable":
        return "oracle_unavailable"
    if fail_to_pass_status not in {"pass", "fail"}:
        return "missing_or_invalid_fail_to_pass_status"
    if pass_to_pass_status not in {"pass", "fail"}:
        return "missing_or_invalid_pass_to_pass_status"
    return "oracle_unavailable"


def _oracle_run_summary(
    result: Mapping[str, Any],
    *,
    repeat_index: int,
    expected_repeats: int,
) -> dict[str, Any]:
    fail_to_pass_status = _status_value(result, "fail_to_pass_status")
    pass_to_pass_status = _status_value(result, "pass_to_pass_status")
    patch_applied = _patch_applied_from_result(result)
    unavailable = bool(
        result.get("oracle_unavailable")
        or result.get("unavailable")
        or str(result.get("status") or "").lower() == "unavailable"
        or fail_to_pass_status == "unavailable"
        or pass_to_pass_status == "unavailable"
        or patch_applied is not True
        or fail_to_pass_status not in {"pass", "fail"}
        or pass_to_pass_status not in {"pass", "fail"}
    )
    reason = ""
    if unavailable:
        reason = _unavailable_reason(
            result,
            fail_to_pass_status=fail_to_pass_status,
            pass_to_pass_status=pass_to_pass_status,
            patch_applied=patch_applied,
        )
    return {
        "repeat_index": repeat_index,
        "expected_repeats": expected_repeats,
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "oracle_unavailable": unavailable,
        "oracle_unavailable_reason": reason,
    }


def repeat_oracle_labels(
    candidates: Sequence[Mapping[str, Any]],
    *,
    repeats: int = 3,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    records_by_instance: Mapping[str, Mapping[str, Any]],
    scripts_dir: str,
) -> dict[str, dict[str, Any]]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    stability: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        instance_id = str(candidate.get("instance_id") or "")
        record = records_by_instance.get(instance_id)
        if record is None:
            raise RuntimeError(
                f"candidate {key} has no matching curated dataset record"
            )
        context = _augment_attempt_with_oracle_context(candidate, record, scripts_dir)
        runs: list[dict[str, Any]] = []
        for repeat_index in range(1, repeats + 1):
            try:
                raw = oracle_runner(context)
                if not isinstance(raw, Mapping):
                    raw = {
                        "oracle_unavailable": True,
                        "oracle_unavailable_reason": "oracle_returned_non_object",
                    }
            except Exception as exc:  # pragma: no cover - exercised through behavior.
                raw = {
                    "oracle_unavailable": True,
                    "oracle_unavailable_reason": f"oracle_exception:{type(exc).__name__}",
                }
            runs.append(
                _oracle_run_summary(
                    raw,
                    repeat_index=repeat_index,
                    expected_repeats=repeats,
                )
            )
        stability[key] = {
            "instance_id": instance_id,
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "candidate_artifact_hash": str(candidate.get("candidate_artifact_hash") or ""),
            "runs": runs,
            "classification": classify_stability(runs),
        }
    return stability


def classify_stability(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"status": "UNAVAILABLE", "reason": "no_repeats"}
    expected_repeats = 0
    if runs:
        expected_repeats = max(
            int(run.get("expected_repeats") or 0)
            for run in runs
            if isinstance(run, Mapping)
        )
    if expected_repeats <= 0:
        return {
            "status": "UNAVAILABLE",
            "reason": "missing_expected_repeats",
        }
    if len(runs) < expected_repeats:
        return {
            "status": "UNAVAILABLE",
            "reason": f"insufficient_repeats:{len(runs)}/{expected_repeats}",
        }
    unavailable_reasons = [
        str(run.get("oracle_unavailable_reason") or "oracle_unavailable")
        for run in runs
        if run.get("oracle_unavailable")
    ]
    if unavailable_reasons:
        return {
            "status": "UNAVAILABLE",
            "reason": "oracle_unavailable:" + unavailable_reasons[0],
        }
    pairs = {
        (
            str(run.get("fail_to_pass_status") or ""),
            str(run.get("pass_to_pass_status") or ""),
        )
        for run in runs
    }
    if len(pairs) == 1:
        fail_to_pass_status, pass_to_pass_status = next(iter(pairs))
        return {
            "status": "STABLE",
            "reason": "",
            "fail_to_pass_status": fail_to_pass_status,
            "pass_to_pass_status": pass_to_pass_status,
        }
    return {
        "status": "UNSTABLE",
        "reason": "unstable_label",
        "observed_status_pairs": [
            {
                "fail_to_pass_status": fail_to_pass_status,
                "pass_to_pass_status": pass_to_pass_status,
            }
            for fail_to_pass_status, pass_to_pass_status in sorted(pairs)
        ],
    }


def _filter_stable_corpus(
    predictions: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    *,
    fail_closed: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    if not predictions:
        raise RuntimeError("empty predictions input; refusing label-stability run")
    stable_predictions: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    unstable_count = 0
    for prediction in predictions:
        key = candidate_key(prediction)
        entry = stability.get(key)
        classification = (
            dict(entry.get("classification") or {})
            if isinstance(entry, Mapping)
            else {"status": "UNAVAILABLE", "reason": "stability_missing"}
        )
        status = str(classification.get("status") or "")
        if status == "STABLE":
            _assert_no_secret_values(prediction, path=key)
            stable_predictions.append(dict(prediction))
            continue
        reason = str(classification.get("reason") or status.lower() or "dropped")
        if status == "UNSTABLE":
            unstable_count += 1
            reason = "unstable_label"
        dropped.append({
            "instance_id": str(prediction.get("instance_id") or ""),
            "candidate_id": str(prediction.get("candidate_id") or ""),
            "candidate_artifact_hash": str(
                prediction.get("candidate_artifact_hash") or ""
            ),
            "reason": reason,
        })
    flake_rate = unstable_count / len(predictions)
    if fail_closed and not stable_predictions:
        raise RuntimeError("all candidates were dropped by label-stability gate")
    return stable_predictions, dropped, flake_rate


def filter_stable_corpus(
    predictions: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    return _filter_stable_corpus(predictions, stability, fail_closed=True)


def build_flake_report(
    *,
    predictions: Sequence[Mapping[str, Any]],
    stable_predictions: Sequence[Mapping[str, Any]],
    dropped: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    flake_rate: float,
    repeats: int,
) -> dict[str, Any]:
    dropped_reasons: dict[str, int] = {}
    for row in dropped:
        reason = str(row.get("reason") or "dropped")
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
    unstable_count = dropped_reasons.get("unstable_label", 0)
    unavailable_count = len(dropped) - unstable_count
    return {
        "schema_version": "supervisor-swebench-pro-label-stability/v1",
        "status": "completed" if stable_predictions else "unavailable",
        "repeats": repeats,
        "summary": {
            "total_evaluated": len(predictions),
            "stable_count": len(stable_predictions),
            "dropped_count": len(dropped),
            "unstable_count": unstable_count,
            "unavailable_count": unavailable_count,
            "flake_rate": flake_rate,
            "dropped_reasons": dropped_reasons,
        },
        "dropped": [dict(row) for row in dropped],
        "stability": [
            {
                "instance_id": str(entry.get("instance_id") or ""),
                "candidate_id": str(entry.get("candidate_id") or ""),
                "candidate_artifact_hash": str(
                    entry.get("candidate_artifact_hash") or ""
                ),
                "classification": dict(entry.get("classification") or {}),
                "runs": [dict(run) for run in entry.get("runs") or []],
            }
            for entry in stability.values()
        ],
        "labels": {
            "benchmark_oracle_kind": "swe_bench_held_out_test_pass_proxy",
            "report_only": True,
            "gate": "label_stability_filter",
        },
        **AUTHORITY_FLAGS,
    }


def run_label_stability(
    *,
    records_path: Path,
    predictions_path: Path,
    scripts_dir: str,
    repeats: int,
    output_path: Path,
    report_path: Path,
    oracle_timeout_s: float,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    records = _read_json_or_jsonl(records_path)
    predictions = _read_jsonl(predictions_path)
    records_by_instance = _records_by_instance(records)
    os.environ["SWEBENCH_PRO_ORACLE_SCRIPTS_DIR"] = scripts_dir
    os.environ["SWEBENCH_PRO_ORACLE_SUBPROCESS_TIMEOUT_S"] = str(oracle_timeout_s)
    repeated = repeat_oracle_labels(
        predictions,
        repeats=repeats,
        oracle_runner=oracle_runner,
        records_by_instance=records_by_instance,
        scripts_dir=scripts_dir,
    )
    stable_predictions, dropped, flake_rate = _filter_stable_corpus(
        predictions,
        repeated,
        fail_closed=False,
    )
    report = build_flake_report(
        predictions=predictions,
        stable_predictions=stable_predictions,
        dropped=dropped,
        stability=repeated,
        flake_rate=flake_rate,
        repeats=repeats,
    )
    _write_json(report_path, report)
    if not stable_predictions:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError("all candidates were dropped by label-stability gate")
    _write_jsonl(output_path, stable_predictions)
    return report


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, help="Curated SWE-bench Pro records")
    parser.add_argument("--predictions", required=True, help="Oracle-labeled JSONL corpus")
    parser.add_argument("--swe-bench-pro-scripts-dir", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", required=True, help="Stable JSONL output path")
    parser.add_argument("--report", required=True, help="Flake report JSON path")
    parser.add_argument("--oracle-timeout-s", type=float, default=3600.0)
    parser.add_argument("--allow-live", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.allow_live:
        print(
            "refusing live SWE-bench Pro oracle re-runs without --allow-live",
            file=sys.stderr,
        )
        return 2
    try:
        run_label_stability(
            records_path=Path(args.records),
            predictions_path=Path(args.predictions),
            scripts_dir=str(args.swe_bench_pro_scripts_dir),
            repeats=int(args.repeats),
            output_path=Path(args.output),
            report_path=Path(args.report),
            oracle_timeout_s=float(args.oracle_timeout_s),
            oracle_runner=run_swe_bench_pro_oracle,
        )
    except Exception as exc:
        print(f"label-stability failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
