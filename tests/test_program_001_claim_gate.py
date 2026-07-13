from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from supervisor.claim_gate import (
    BUSINESS_VALUE_PROTOCOL_SCHEMA_VERSION,
    CANARY_RESULT_SCHEMA_VERSION,
    ClaimLevel,
    DEFAULT_CLAIM_RULES,
    FROZEN_CONTROL_RECEIPT_SCHEMA_VERSION,
    HUMAN_APPROVAL_RECEIPT_SCHEMA_VERSION,
    INCREMENTAL_COST_PROVENANCE_SCHEMA_VERSION,
    InvalidClaimGateReceiptError,
    MANAGED_CLAIM_FIELDS,
    ManualClaimFlagError,
    ROI_ANALYSIS_SCHEMA_VERSION,
    ROLLBACK_RECEIPT_SCHEMA_VERSION,
    SEALED_HOLDOUT_RECEIPT_SCHEMA_VERSION,
    SHADOW_RESULT_SCHEMA_VERSION,
    STRATA_REPLICATION_ANALYSIS_SCHEMA_VERSION,
    UnsupportedClaimError,
)
from tests.test_claim_gate import (
    ClaimGate,
    _attested_hidden_verifier,
    _auto_improvement_authoritative_bundle,
    _authoritative_causal_bundle,
    _claim_gate_kwargs,
    _portable_authoritative_bundle,
    _roi_authoritative_bundle,
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
            **_attested_hidden_verifier(verifier_result),
        },
    }


def _causal_bundle(evidence_root: Path):
    return _authoritative_causal_bundle(evidence_root)


def _portable_bundle(evidence_root: Path):
    return _portable_authoritative_bundle(evidence_root)


def _roi_bundle(evidence_root: Path):
    return _roi_authoritative_bundle(evidence_root)


def test_l3_requires_a_positive_randomized_powered_b_vs_c_result(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _causal_bundle(tmp_path)
    causal_result = bundle["randomized_powered_b_vs_c"]
    assert isinstance(causal_result, dict)

    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L3

    causal_result["comparison"] = "A_vs_B"
    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L2


def test_l4_requires_replication_across_distinct_strata(tmp_path: Path) -> None:
    bundle, ledger_resolver = _portable_bundle(tmp_path)
    replication = bundle["strata_replication"]
    assert isinstance(replication, dict)

    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L4

    replication["strata"] = ["python"]
    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L3

    replication["strata"] = ["python", "unity"]
    replication["model_families"] = replication["model_families"][:2]
    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L3


def test_l5_requires_measured_operating_cost_and_positive_roi(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _roi_bundle(tmp_path)
    operating_cost = bundle["operating_cost"]
    assert isinstance(operating_cost, dict)

    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L5

    operating_cost["supports_positive_roi"] = False
    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L4


def test_l6_requires_all_linked_auto_improvement_receipts(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _auto_improvement_authoritative_bundle(
        tmp_path
    )

    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L6

    del bundle["shadow_result"]
    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L5


def test_l6_rejects_legacy_boolean_plus_hash_control_evidence(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _roi_bundle(tmp_path)
    for name, state_key in (
        ("frozen_control", "frozen"),
        ("sealed_holdout", "sealed"),
        ("shadow_result", "passed"),
        ("human_approval", "approved"),
        ("canary", "passed"),
        ("rollback_receipt", "passed"),
    ):
        descriptor = _write_artifact(
            tmp_path,
            f"artifacts/legacy-{name}.json",
        )
        bundle[name] = {state_key: True, **descriptor}

    assert ClaimGate.max_claim_level(
        bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    ) == ClaimLevel.L5


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
    bundle, ledger_resolver = _causal_bundle(tmp_path)
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
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
        )


def test_validate_report_rejects_nested_managed_claim_fields(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ManualClaimFlagError,
        match=(
            "sections\\[0\\]\\.authority_flags"
            "\\.powered_improvement_claim_allowed is derived by ClaimGate"
        ),
    ):
        ClaimGate.validate_report(
            {
                "sections": [
                    {
                        "authority_flags": {
                            "powered_improvement_claim_allowed": False,
                        }
                    }
                ]
            },
            _outcome_bundle(tmp_path),
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
    causal_bundle, ledger_resolver = _causal_bundle(tmp_path)
    causal_report = ClaimGate.derive_report(
        {"schema_version": "example-report/v1"},
        causal_bundle,
        evidence_root=tmp_path,
        ledger_verification_resolver=ledger_resolver,
    )

    assert outcome_report["improvement_claim_allowed"] is False
    assert outcome_report["powered_improvement_claim_allowed"] is False
    assert outcome_report["claim_gate"]["max_claim_level"] == "L2"
    assert causal_report["improvement_claim_allowed"] is True
    assert causal_report["powered_improvement_claim_allowed"] is True
    assert causal_report["claim_gate"]["max_claim_level"] == "L3"


def test_validate_derived_report_recomputes_receipt_and_managed_flags(
    tmp_path: Path,
) -> None:
    bundle, ledger_resolver = _causal_bundle(tmp_path)
    report = ClaimGate.derive_report(
        {"schema_version": "example-report/v1"},
        bundle,
        evidence_root=tmp_path,
        **_claim_gate_kwargs(ledger_resolver),
    )

    assert (
        ClaimGate.validate_derived_report(
            report,
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(ledger_resolver),
        )
        == ClaimLevel.L3
    )

    forged_receipt = {
        **report,
        "claim_gate": {
            **report["claim_gate"],
            "evidence_bundle_sha256": "f" * 64,
        },
    }
    with pytest.raises(
        InvalidClaimGateReceiptError,
        match="evidence bundle hash does not match",
    ):
        ClaimGate.validate_derived_report(
            forged_receipt,
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(ledger_resolver),
        )

    forged_flags = {
        **report,
        "powered_improvement_claim_allowed": False,
    }
    with pytest.raises(
        InvalidClaimGateReceiptError,
        match="powered_improvement_claim_allowed does not match",
    ):
        ClaimGate.validate_derived_report(
            forged_flags,
            bundle,
            evidence_root=tmp_path,
            **_claim_gate_kwargs(ledger_resolver),
        )


def test_govern_report_receipts_legacy_nested_claim_fields() -> None:
    report = ClaimGate.govern_report(
        {
            "schema_version": "legacy-report/v1",
            "authority_flags": {
                "improvement_claim_allowed": False,
                "powered_improvement_claim_allowed": False,
            },
        }
    )

    assert report["improvement_claim_allowed"] is False
    assert report["powered_improvement_claim_allowed"] is False
    assert report["claim_gate"]["managed_field_paths"] == [
        "authority_flags.improvement_claim_allowed",
        "authority_flags.powered_improvement_claim_allowed",
        "improvement_claim_allowed",
        "powered_improvement_claim_allowed",
    ]
    assert ClaimGate.validate_derived_report(report) is None


def test_govern_report_rejects_caller_selected_nested_authority() -> None:
    with pytest.raises(
        ManualClaimFlagError,
        match=(
            "authority_flags.improvement_claim_allowed does not match "
            "ClaimGate-derived authority"
        ),
    ):
        ClaimGate.govern_report(
            {
                "authority_flags": {
                    "improvement_claim_allowed": True,
                }
            }
        )


def test_governed_report_detects_nested_authority_tampering() -> None:
    report = ClaimGate.govern_report(
        {
            "authority_flags": {
                "improvement_claim_allowed": False,
            }
        }
    )
    report["authority_flags"]["improvement_claim_allowed"] = True

    with pytest.raises(
        InvalidClaimGateReceiptError,
        match="authority_flags.improvement_claim_allowed does not match",
    ):
        ClaimGate.validate_derived_report(report)


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


def test_nested_free_text_causal_claim_is_rejected_below_l3(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        UnsupportedClaimError,
        match="Supervisor improves outcomes requires L3; evidence supports L2",
    ):
        ClaimGate.validate_report(
            {
                "summary": {
                    "sections": [
                        {
                            "heading": "Conclusion",
                            "body": "Supervisor improves outcomes.",
                        }
                    ]
                }
            },
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )


@pytest.mark.parametrize(
    "claim_text",
    [
        "B produces more successful tasks than C.",
        "The supervisor yields a higher pass rate than direct execution.",
        "Our harness delivers better benchmark outcomes than the baseline.",
    ],
)
def test_comparative_outcome_paraphrases_require_l3(
    tmp_path: Path,
    claim_text: str,
) -> None:
    with pytest.raises(UnsupportedClaimError, match="requires L3"):
        ClaimGate.validate_report(
            {"summary": claim_text},
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "B is scheduled before C.",
        "The supervisor report compares outcome counts.",
        "The harness may improve after more experiments.",
    ],
)
def test_ordinary_noncausal_prose_is_not_reclassified(
    tmp_path: Path,
    ordinary_text: str,
) -> None:
    assert (
        ClaimGate.validate_report(
            {"summary": ordinary_text},
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )
        == ClaimLevel.L2
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
    bundle, ledger_resolver = _causal_bundle(tmp_path)
    assert (
        ClaimGate.validate_report(
            {"claims": ["CLAIM-HARNESS-L3-CAUSAL-IMPROVEMENT"]},
            bundle,
            evidence_root=tmp_path,
            ledger_verification_resolver=ledger_resolver,
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
    assert ladder["schema_version"] == "harness-v1-claim-ladder/v2"
    levels = {level["id"]: level for level in ladder["levels"]}
    assert (
        levels["L4"]["predicate"]["resolved_analysis"]["schema_version"]
        == STRATA_REPLICATION_ANALYSIS_SCHEMA_VERSION
    )
    l5_analysis = levels["L5"]["predicate"]["resolved_analysis"]
    assert l5_analysis["schema_version"] == ROI_ANALYSIS_SCHEMA_VERSION
    assert (
        l5_analysis["business_value_protocol"]["schema_version"]
        == BUSINESS_VALUE_PROTOCOL_SCHEMA_VERSION
    )
    assert (
        l5_analysis["incremental_cost_provenance"]["schema_version"]
        == INCREMENTAL_COST_PROVENANCE_SCHEMA_VERSION
    )
    l6_records = levels["L6"]["predicate"]["all_records"]
    assert {
        name: details["schema_version"]
        for name, details in l6_records.items()
    } == {
        "frozen_control": FROZEN_CONTROL_RECEIPT_SCHEMA_VERSION,
        "sealed_holdout": SEALED_HOLDOUT_RECEIPT_SCHEMA_VERSION,
        "shadow_result": SHADOW_RESULT_SCHEMA_VERSION,
        "rollback_receipt": ROLLBACK_RECEIPT_SCHEMA_VERSION,
        "human_approval": HUMAN_APPROVAL_RECEIPT_SCHEMA_VERSION,
        "canary": CANARY_RESULT_SCHEMA_VERSION,
    }
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
