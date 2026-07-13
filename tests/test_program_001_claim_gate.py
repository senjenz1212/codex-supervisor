from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from supervisor.claim_gate import (
    ClaimGate,
    ClaimLevel,
    DEFAULT_CLAIM_RULES,
    MANAGED_CLAIM_FIELDS,
    ManualClaimFlagError,
    UnsupportedClaimError,
)


def _write_artifact(evidence_root: Path, ref: str) -> dict[str, str]:
    content = f"{ref}\n".encode()
    path = evidence_root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "ref": ref,
        "sha256": sha256(content).hexdigest(),
    }


def _outcome_bundle(evidence_root: Path) -> dict[str, object]:
    run_manifest = _write_artifact(
        evidence_root,
        "artifacts/run-manifest.json",
    )
    artifact_manifest = _write_artifact(
        evidence_root,
        "artifacts/artifact-manifest.json",
    )
    replay = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay.json",
    )
    trace = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay-trace.jsonl",
    )
    verifier_result = _write_artifact(
        evidence_root,
        "artifacts/hidden-verifier-result.json",
    )
    return {
        "pins": {
            "repository": "example/repository@abc123",
            "dataset": "fixture-replay@v1",
        },
        "hashes": {
            "run_manifest_sha256": run_manifest["sha256"],
            "artifact_manifest_sha256": artifact_manifest["sha256"],
        },
        "artifacts": [run_manifest, artifact_manifest, replay],
        "traceable_detector": {
            "detector_id": "fixture-replay-detector/v1",
            "trace_ref": trace["ref"],
            "trace_sha256": trace["sha256"],
        },
        "independent_hidden_verifier": {
            "verifier_id": "hidden-verifier/v1",
            "independent": True,
            "hidden": True,
            "result_ref": verifier_result["ref"],
            "result_sha256": verifier_result["sha256"],
        },
    }


def _causal_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _outcome_bundle(evidence_root)
    analysis = _write_artifact(
        evidence_root,
        "artifacts/b-vs-c-analysis.json",
    )
    bundle["randomized_powered_b_vs_c"] = {
        "comparison": "B_vs_C",
        "randomized": True,
        "powered": True,
        "supports_improvement": True,
        "analysis_ref": analysis["ref"],
        "analysis_sha256": analysis["sha256"],
    }
    return bundle


def _portable_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _causal_bundle(evidence_root)
    analysis = _write_artifact(
        evidence_root,
        "artifacts/strata-replication.json",
    )
    bundle["strata_replication"] = {
        "replicated": True,
        "strata": ["python", "unity"],
        "model_families": [
            {"family": "anthropic", "pinned": True, "seen_by_optimizer": True},
            {"family": "openai", "pinned": True, "seen_by_optimizer": True},
            {"family": "google", "pinned": True, "seen_by_optimizer": False},
        ],
        "analysis_ref": analysis["ref"],
        "analysis_sha256": analysis["sha256"],
    }
    return bundle


def _roi_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _portable_bundle(evidence_root)
    analysis = _write_artifact(
        evidence_root,
        "artifacts/roi-analysis.json",
    )
    bundle["operating_cost"] = {
        "measured": True,
        "cost_usd": 12.5,
        "supports_positive_roi": True,
        "analysis_ref": analysis["ref"],
        "analysis_sha256": analysis["sha256"],
    }
    return bundle


def test_l3_requires_a_positive_randomized_powered_b_vs_c_result(
    tmp_path: Path,
) -> None:
    bundle = _causal_bundle(tmp_path)
    causal_result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(causal_result, dict)

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L3

    causal_result["comparison"] = "A_vs_B"
    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L2


def test_l4_requires_replication_across_distinct_strata(tmp_path: Path) -> None:
    bundle = _portable_bundle(tmp_path)
    replication = bundle["strata_replication"]
    assert isinstance(replication, dict)

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L4

    replication["strata"] = ["python"]
    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L3

    replication["strata"] = ["python", "unity"]
    replication["model_families"] = replication["model_families"][:2]
    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L3


def test_l5_requires_measured_operating_cost_and_positive_roi(
    tmp_path: Path,
) -> None:
    bundle = _roi_bundle(tmp_path)
    operating_cost = bundle["operating_cost"]
    assert isinstance(operating_cost, dict)

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L5

    operating_cost["supports_positive_roi"] = False
    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L4


def test_l6_requires_frozen_control_sealed_holdout_and_passing_canary(
    tmp_path: Path,
) -> None:
    bundle = _roi_bundle(tmp_path)
    frozen_control = _write_artifact(tmp_path, "artifacts/frozen-control.json")
    sealed_holdout = _write_artifact(tmp_path, "artifacts/sealed-holdout.json")
    canary = _write_artifact(tmp_path, "artifacts/canary.json")
    bundle.update(
        {
            "frozen_control": {
                "frozen": True,
                **frozen_control,
            },
            "sealed_holdout": {
                "sealed": True,
                **sealed_holdout,
            },
            "canary": {
                "passed": True,
                **canary,
            },
        }
    )

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L6

    del bundle["canary"]
    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) == ClaimLevel.L5


def test_report_producer_cannot_manually_set_improvement_claim_flag(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ManualClaimFlagError,
        match="improvement_claim_allowed is derived by ClaimGate",
    ):
        ClaimGate.derive_report(
            {"improvement_claim_allowed": True},
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )


def test_nested_manual_claim_flag_is_also_rejected(tmp_path: Path) -> None:
    with pytest.raises(
        ManualClaimFlagError,
        match="authority_flags.powered_improvement_claim_allowed is derived by ClaimGate",
    ):
        ClaimGate.derive_report(
            {
                "authority_flags": {
                    "powered_improvement_claim_allowed": True,
                }
            },
            _causal_bundle(tmp_path),
            evidence_root=tmp_path,
        )


def test_report_improvement_flags_are_derived_from_claim_level(
    tmp_path: Path,
) -> None:
    outcome_report = ClaimGate.derive_report(
        {"schema_version": "example-report/v1"},
        _outcome_bundle(tmp_path),
        evidence_root=tmp_path,
    )
    causal_report = ClaimGate.derive_report(
        {"schema_version": "example-report/v1"},
        _causal_bundle(tmp_path),
        evidence_root=tmp_path,
    )

    assert outcome_report["improvement_claim_allowed"] is False
    assert outcome_report["powered_improvement_claim_allowed"] is False
    assert outcome_report["claim_gate"]["max_claim_level"] == "L2"
    assert causal_report["improvement_claim_allowed"] is True
    assert causal_report["powered_improvement_claim_allowed"] is True
    assert causal_report["claim_gate"]["max_claim_level"] == "L3"


def test_report_asserting_forbidden_improvement_claim_is_rejected(
    tmp_path: Path,
) -> None:
    fixture_replay = _outcome_bundle(tmp_path)
    del fixture_replay["independent_hidden_verifier"]

    with pytest.raises(
        UnsupportedClaimError,
        match="supervisor improves outcomes requires L3; evidence supports L1",
    ):
        ClaimGate.validate_report(
            {"claims": ["supervisor improves outcomes"]},
            fixture_replay,
            evidence_root=tmp_path,
        )


def test_fixture_replay_report_cannot_assert_l3(tmp_path: Path) -> None:
    fixture_replay = _outcome_bundle(tmp_path)
    del fixture_replay["independent_hidden_verifier"]

    with pytest.raises(
        UnsupportedClaimError,
        match="asserted claim level L3 exceeds evidence support L1",
    ):
        ClaimGate.validate_report(
            {"asserted_claim_level": "L3"},
            fixture_replay,
            evidence_root=tmp_path,
        )


def test_causal_evidence_allows_registered_improvement_claim(
    tmp_path: Path,
) -> None:
    assert (
        ClaimGate.validate_report(
            {"claims": ["CLAIM-HARNESS-L3-CAUSAL-IMPROVEMENT"]},
            _causal_bundle(tmp_path),
            evidence_root=tmp_path,
        )
        == ClaimLevel.L3
    )


def test_producers_do_not_literal_assign_claim_gate_owned_flags() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    managed_fields = {
        "improvement_claim_allowed",
        "powered_improvement_claim_allowed",
    }
    violations: list[str] = []

    for source_root in ("supervisor", "scripts"):
        for path in sorted((repository_root / source_root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value in managed_fields
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, bool)
                        ):
                            violations.append(
                                f"{path.relative_to(repository_root)}:{value.lineno}"
                            )
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, bool)
                ):
                    continue
                for target in targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value in managed_fields
                    ):
                        violations.append(
                            f"{path.relative_to(repository_root)}:{node.lineno}"
                        )

    assert violations == []


def test_program_pack_matches_runtime_claim_registry() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    program_root = repository_root / "docs" / "program" / "harness-v1"
    ladder = yaml.safe_load(
        (program_root / "claim-ladder.yaml").read_text(encoding="utf-8")
    )
    claims = yaml.safe_load(
        (program_root / "claims.yaml").read_text(encoding="utf-8")
    )
    legacy_map = yaml.safe_load(
        (program_root / "legacy-map.yaml").read_text(encoding="utf-8")
    )

    assert [level["id"] for level in ladder["levels"]] == [
        level.value for level in ClaimLevel
    ]
    assert set(ladder["managed_outputs"]) == set(MANAGED_CLAIM_FIELDS)
    assert {
        name: details["minimum_level"]
        for name, details in ladder["managed_outputs"].items()
    } == {name: "L3" for name in MANAGED_CLAIM_FIELDS}
    assert {
        item["id"]: item["required_level"]
        for item in claims["claims"]
    } == {
        rule.claim_id: rule.required_level.value
        for rule in DEFAULT_CLAIM_RULES
    }
    assert all(
        (repository_root / item["path"]).is_file()
        for item in legacy_map["entries"]
    )
