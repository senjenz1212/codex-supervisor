from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.claim_gate import (
    ClaimGate,
    ClaimLevel,
    UnsupportedClaimError,
)


def _write_artifact(
    evidence_root: Path,
    ref: str,
    content: bytes,
) -> dict[str, str]:
    path = evidence_root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "ref": ref,
        "sha256": sha256(content).hexdigest(),
    }


def _fixture_replay_bundle(evidence_root: Path) -> dict[str, object]:
    run_manifest = _write_artifact(
        evidence_root,
        "artifacts/run-manifest.json",
        b'{"run_id":"fixture-replay"}\n',
    )
    artifact_manifest = _write_artifact(
        evidence_root,
        "artifacts/artifact-manifest.json",
        b'{"artifacts":["fixture-replay.json"]}\n',
    )
    replay = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay.json",
        b'{"status":"passed"}\n',
    )
    trace = _write_artifact(
        evidence_root,
        "artifacts/fixture-replay-trace.jsonl",
        b'{"event":"completed"}\n',
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
    }


def _outcome_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _fixture_replay_bundle(evidence_root)
    result = _write_artifact(
        evidence_root,
        "artifacts/hidden-verifier-result.json",
        b'{"verdict":"passed"}\n',
    )
    bundle["independent_hidden_verifier"] = {
        "verifier_id": "hidden-verifier/v1",
        "independent": True,
        "hidden": True,
        "result_ref": result["ref"],
        "result_sha256": result["sha256"],
    }
    return bundle


def _causal_bundle(evidence_root: Path) -> dict[str, object]:
    bundle = _outcome_bundle(evidence_root)
    analysis = _write_artifact(
        evidence_root,
        "artifacts/b-vs-c-analysis.json",
        b'{"supports_improvement":true}\n',
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


def test_fixture_replay_bundle_resolves_to_l1(tmp_path: Path) -> None:
    assert (
        ClaimGate.max_claim_level(
            _fixture_replay_bundle(tmp_path),
            evidence_root=tmp_path,
        )
        == ClaimLevel.L1
    )


def test_valid_looking_evidence_without_a_resolver_fails_closed(
    tmp_path: Path,
) -> None:
    assert ClaimGate.max_claim_level(_fixture_replay_bundle(tmp_path)) is None


def test_missing_or_hash_mismatched_trace_cannot_raise_l1(tmp_path: Path) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    trace_path = tmp_path / "artifacts/fixture-replay-trace.jsonl"

    trace_path.unlink()
    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L0
    )

    trace_path.write_bytes(b'{"event":"tampered"}\n')
    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L0
    )


def test_unresolved_declared_hash_cannot_raise_l0(tmp_path: Path) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    hashes = bundle["hashes"]
    assert isinstance(hashes, dict)
    hashes["unresolved_sha256"] = "f" * 64

    assert ClaimGate.max_claim_level(bundle, evidence_root=tmp_path) is None


def test_repeated_declared_digest_can_share_one_verified_artifact(
    tmp_path: Path,
) -> None:
    bundle = _fixture_replay_bundle(tmp_path)
    hashes = bundle["hashes"]
    assert isinstance(hashes, dict)
    hashes["run_manifest_copy_sha256"] = hashes["run_manifest_sha256"]

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L1
    )


def test_l2_requires_an_independent_hidden_verifier(tmp_path: Path) -> None:
    bundle = _outcome_bundle(tmp_path)
    verifier = bundle["independent_hidden_verifier"]
    assert isinstance(verifier, dict)

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )

    verifier["independent"] = False
    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L1
    )


@pytest.mark.parametrize(
    "optimizer_unseen_family",
    [
        {"family": "", "pinned": True, "seen_by_optimizer": False},
        {"family": "anthropic", "pinned": True, "seen_by_optimizer": False},
        {"family": "optimizer-holdout", "pinned": False, "seen_by_optimizer": False},
    ],
)
def test_l4_optimizer_unseen_family_is_named_distinct_pinned_and_counted(
    tmp_path: Path,
    optimizer_unseen_family: dict[str, object],
) -> None:
    bundle = _causal_bundle(tmp_path)
    replication = _write_artifact(
        tmp_path,
        "artifacts/strata-replication.json",
        b'{"replicated":true}\n',
    )
    bundle["strata_replication"] = {
        "replicated": True,
        "strata": ["python", "unity"],
        "model_families": [
            {"family": "anthropic", "pinned": True, "seen_by_optimizer": True},
            {"family": "openai", "pinned": True, "seen_by_optimizer": True},
            {"family": "google", "pinned": True, "seen_by_optimizer": True},
            optimizer_unseen_family,
        ],
        "analysis_ref": replication["ref"],
        "analysis_sha256": replication["sha256"],
    }

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L3
    )


def test_low_level_claim_id_cannot_override_improvement_text(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        UnsupportedClaimError,
        match="Supervisor improves outcomes requires L3; evidence supports L2",
    ):
        ClaimGate.validate_report(
            {
                "claims": [
                    {
                        "claim_id": "CLAIM-HARNESS-L0-INTEGRITY",
                        "text": "Supervisor improves outcomes",
                    }
                ]
            },
            _outcome_bundle(tmp_path),
            evidence_root=tmp_path,
        )


def test_current_tracer_evidence_remains_capped_at_l2(tmp_path: Path) -> None:
    bundle = _outcome_bundle(tmp_path)

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )
    assert ClaimGate.derived_claim_flags(
        bundle,
        evidence_root=tmp_path,
    ) == {
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
    }


def test_custom_evidence_resolver_can_supply_verified_bytes(
    tmp_path: Path,
) -> None:
    bundle = _outcome_bundle(tmp_path)
    resolved = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert (
        ClaimGate.max_claim_level(
            bundle,
            evidence_resolver=resolved.get,
        )
        == ClaimLevel.L2
    )


def test_mismatched_causal_analysis_hash_stops_at_l2(tmp_path: Path) -> None:
    bundle = _causal_bundle(tmp_path)
    analysis = bundle["randomized_powered_b_vs_c"]
    assert isinstance(analysis, dict)
    analysis["analysis_sha256"] = "0" * 64

    assert (
        ClaimGate.max_claim_level(bundle, evidence_root=tmp_path)
        == ClaimLevel.L2
    )
