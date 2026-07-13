from __future__ import annotations

import hashlib
import hmac
import json
import time
from copy import deepcopy

import pytest

from supervisor.pilot_readiness import (
    EvidencePin,
    FrozenPilotProtocol,
    PILOT_PROTOCOL_SCHEMA_VERSION,
    PilotReadinessError,
    ReceiptAttestation,
    freeze_pilot_protocol,
    receipt_attestation_payload,
    validate_pilot_execution_authorization,
    validate_pilot_readiness,
)


SHA = "a" * 64
COMMIT = "b" * 40
AUTHORITY_ID = "pilot-release-authority"
AUTHORITY_KEY = b"pilot-release-authority-test-key"


class _HmacVerifier:
    def verify(self, payload: bytes, signature: dict) -> bool:
        expected = hmac.new(
            AUTHORITY_KEY,
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(
            str(signature.get("hmac_sha256") or ""),
            expected,
        )


TRUSTED_RECEIPT_VERIFIERS = {AUTHORITY_ID: _HmacVerifier()}


def _task_identity(canonical_task_key: str) -> dict[str, str]:
    return {
        "repo": "https://github.com/Unity-Technologies/example.git",
        "canonical_repo_id": (
            "https://github.com/Unity-Technologies/example.git"
        ),
        "revision": "1" * 40,
        "dataset_hash": "2" * 64,
        "split_hash": "3" * 64,
        "canonical_task_key": canonical_task_key,
    }


def _protocol_payload() -> dict:
    return {
        "schema_version": PILOT_PROTOCOL_SCHEMA_VERSION,
        "experiment_id": "pilot-001",
        "run_id": "5a1f419c-8458-4d48-ab8c-8a288de1846d",
        "client_token": "harness-v1-pilot-001",
        "commit_sha": COMMIT,
        "task_ids": ["task-1", "task-2"],
        "task_identities": {
            "task-1": _task_identity("pilot-task-1"),
            "task-2": _task_identity("pilot-task-2"),
        },
        "task_families": {
            "task-1": "generic",
            "task-2": "unity",
        },
        "runtimes": ["claude_code", "codex"],
        "task_count": 2,
        "confirmation_task_ids": ["confirm-1"],
        "confirmation_task_identities": {
            "confirm-1": _task_identity("confirmation-task-1"),
        },
        "sealed_holdout_task_ids": ["holdout-1"],
        "sealed_holdout_task_identities": {
            "holdout-1": _task_identity("holdout-task-1"),
        },
        "portability_task_ids": ["portable-1"],
        "portability_task_identities": {
            "portable-1": _task_identity("portability-task-1"),
        },
        "arm_budgets": {
            "A": {
                "max_attempts": 1,
                "max_tokens": 1000,
                "max_cost_usd": 1.0,
                "timeout_s": 60,
            },
            "B": {
                "max_attempts": 2,
                "max_tokens": 2000,
                "max_cost_usd": 2.0,
                "timeout_s": 120,
            },
            "C": {
                "max_attempts": 2,
                "max_tokens": 2000,
                "max_cost_usd": 2.0,
                "timeout_s": 120,
            },
        },
        "assignment": {
            "version": "assignment/v1",
            "sticky_key_algorithm": "HMAC-SHA256",
            "key_custodian": "named-operator",
            "persist_before_execution": True,
        },
        "stop_rule": {
            "outcome_dependent": False,
            "exact_task_count": 2,
            "run_until_discordant_pairs": False,
        },
        "retry_policy": {
            "treatment_failure_is_itt_failure": True,
            "max_common_infra_block_reruns": 1,
            "selective_arm_rerun": False,
        },
        "alternative_b_win_rate": 0.65,
        "target_power": 0.90,
        "discordance_bound_method": "wilson-lower-95",
        "runtime_pins": {
            "claude_code": {"sha256": SHA},
            "codex": {"sha256": SHA},
        },
        "model_pins": {"A": {"sha256": SHA}, "B": {"sha256": SHA}, "C": {"sha256": SHA}},
        "prompt_pins": {"A": {"sha256": SHA}, "B": {"sha256": SHA}, "C": {"sha256": SHA}},
        "tool_contract_pins": {"tools": {"sha256": SHA}},
        "cli_pins": {
            "claude_code": {"sha256": SHA},
            "codex": {"sha256": SHA},
        },
        "image_pins": {"generic": {"sha256": SHA}, "unity": {"sha256": SHA}},
        "verifier_pins": {"generic": {"sha256": SHA}, "unity": {"sha256": SHA}},
        "network_resource_policy": {"policy": {"sha256": SHA}},
    }


def _receipt_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _signed_pin(
    name: str,
    blob: bytes,
    protocol_hash: str,
) -> EvidencePin:
    ref = f"receipt://{name}"
    digest = hashlib.sha256(blob).hexdigest()
    signed_payload = receipt_attestation_payload(
        receipt_name=name,
        evidence_ref=ref,
        evidence_sha256=digest,
        protocol_hash=protocol_hash,
        authority_id=AUTHORITY_ID,
    )
    return EvidencePin(
        ref=ref,
        sha256=digest,
        attestation=ReceiptAttestation(
            authority_id=AUTHORITY_ID,
            signature={
                "hmac_sha256": hmac.new(
                    AUTHORITY_KEY,
                    signed_payload,
                    hashlib.sha256,
                ).hexdigest()
            },
        ),
    )


def _receipts(
    protocol_hash: str,
    *,
    signed: bool = True,
) -> tuple[dict[str, EvidencePin], dict[str, bytes]]:
    payloads = {
        "worktree": {
            "clean": True,
            "status_porcelain": "",
            "head_commit": COMMIT,
        },
        "component_gate": {"status": "passed", "commit_sha": COMMIT},
        "operational_tracer": {
            "status": "passed",
            "mode": "operational",
            "claim_level": "L2",
            "ledger_valid": True,
            "trace_closed": True,
            "hidden_verifier_isolated": True,
            "commit_sha": COMMIT,
            "task_families": ["generic", "unity"],
            "runtimes": ["claude_code", "codex"],
            "external_provider_calls": True,
        },
        "runtime_claude_code": {
            "status": "available",
            "runtime": "claude_code",
            "commit_sha": COMMIT,
            "version": "1.2.3",
            "executable_sha256": SHA,
            "runtime_implementation_sha256": SHA,
        },
        "runtime_codex": {
            "status": "available",
            "runtime": "codex",
            "commit_sha": COMMIT,
            "version": "0.194.0",
            "executable_sha256": SHA,
            "runtime_implementation_sha256": SHA,
        },
        "verifier_generic": {
            "status": "available",
            "task_family": "generic",
            "hidden": True,
            "independent": True,
            "commit_sha": COMMIT,
            "verifier_id": "swebench-official",
            "verifier_version": "1",
            "verifier_implementation_sha256": SHA,
        },
        "verifier_unity": {
            "status": "available",
            "task_family": "unity",
            "hidden": True,
            "independent": True,
            "commit_sha": COMMIT,
            "verifier_id": "unity-test-framework",
            "verifier_version": "1",
            "verifier_implementation_sha256": SHA,
        },
        "budget_authorization": {
            "status": "authorized",
            "protocol_hash": protocol_hash,
            "authorized_by": "Sam Zhang",
            "max_cost_usd": 100.0,
            "max_wall_time_s": 3600,
            "credentials_authorized": True,
            "storage_authorized": True,
            "valid_from_ms": int(time.time() * 1000) - 60_000,
            "valid_until_ms": int(time.time() * 1000) + 3_600_000,
        },
        "reviewer_acceptance": {
            "status": "accepted",
            "protocol_hash": protocol_hash,
            "commit_sha": COMMIT,
            "reviewers": [
                {"reviewer": "claude_code", "decision": "accept"},
                {"reviewer": "cursor_sdk", "decision": "accept"},
            ],
        },
    }
    blobs = {
        f"receipt://{name}": _receipt_bytes(payload)
        for name, payload in payloads.items()
    }
    pins: dict[str, EvidencePin] = {}
    for name in payloads:
        blob = blobs[f"receipt://{name}"]
        pins[name] = (
            _signed_pin(name, blob, protocol_hash)
            if signed
            else EvidencePin(
                ref=f"receipt://{name}",
                sha256=hashlib.sha256(blob).hexdigest(),
            )
        )
    return pins, blobs


def test_frozen_protocol_and_pinned_receipts_can_reach_ready():
    protocol = freeze_pilot_protocol(_protocol_payload())
    pins, blobs = _receipts(protocol.protocol_hash)

    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )

    assert report.ready is True
    assert report.findings == ()
    assert len(report.verified_receipts) == 9
    assert len(report.protocol_hash) == 64
    assert len(report.task_set_hash) == 64
    assert len(report.report_hash) == 64
    assert report.to_dict()["status"] == "ready"


def test_frozen_protocol_is_deeply_immutable_and_cannot_be_bypassed():
    source = _protocol_payload()
    protocol = freeze_pilot_protocol(source)
    source["arm_budgets"]["A"]["max_tokens"] = 999_999

    assert protocol.payload["arm_budgets"]["A"]["max_tokens"] == 1000
    with pytest.raises(TypeError):
        protocol.payload["arm_budgets"]["A"]["max_tokens"] = 2
    with pytest.raises(
        PilotReadinessError,
        match="freeze_pilot_protocol",
    ):
        FrozenPilotProtocol(
            payload={},
            protocol_hash="0" * 64,
            task_set_hash="0" * 64,
        )


def test_readiness_recomputes_protocol_hash_before_resolving_receipts():
    protocol = freeze_pilot_protocol(_protocol_payload())
    object.__setattr__(protocol, "protocol_hash", "0" * 64)
    resolver_calls = 0

    def resolver(_ref: str):
        nonlocal resolver_calls
        resolver_calls += 1
        return None

    with pytest.raises(PilotReadinessError, match="integrity"):
        validate_pilot_readiness(
            protocol,
            receipts={},
            evidence_resolver=resolver,
        )

    assert resolver_calls == 0


def test_non_unity_single_runtime_protocol_drives_readiness_requirements():
    payload = _protocol_payload()
    payload["task_families"] = {
        "task-1": "generic",
        "task-2": "generic",
    }
    payload["runtimes"] = ["codex"]
    payload["runtime_pins"] = {"codex": {"sha256": SHA}}
    payload["cli_pins"] = {"codex": {"sha256": SHA}}
    payload["image_pins"] = {"generic": {"sha256": SHA}}
    payload["verifier_pins"] = {"generic": {"sha256": SHA}}
    protocol = freeze_pilot_protocol(payload)
    pins, blobs = _receipts(protocol.protocol_hash)
    pins.pop("runtime_claude_code")
    pins.pop("verifier_unity")
    blobs.pop("receipt://runtime_claude_code")
    blobs.pop("receipt://verifier_unity")
    tracer = json.loads(blobs["receipt://operational_tracer"])
    tracer["task_families"] = ["generic"]
    tracer["runtimes"] = ["codex"]
    blobs["receipt://operational_tracer"] = _receipt_bytes(tracer)
    pins["operational_tracer"] = _signed_pin(
        "operational_tracer",
        blobs["receipt://operational_tracer"],
        protocol.protocol_hash,
    )

    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )

    assert report.ready is True
    assert len(report.verified_receipts) == 7


def test_execution_authorization_rechecks_report_freshness_and_task_binding():
    now_ms = int(time.time() * 1000)
    protocol = freeze_pilot_protocol(_protocol_payload())
    pins, blobs = _receipts(protocol.protocol_hash)
    authorization = json.loads(blobs["receipt://budget_authorization"])
    authorization["valid_from_ms"] = now_ms - 1_000
    authorization["valid_until_ms"] = now_ms + 1_000
    blobs["receipt://budget_authorization"] = _receipt_bytes(authorization)
    pins["budget_authorization"] = _signed_pin(
        "budget_authorization",
        blobs["receipt://budget_authorization"],
        protocol.protocol_hash,
    )
    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
        now_ms=now_ms,
    )
    task = {
        "task_id": "task-1",
        "task_family": "generic",
        **_task_identity("pilot-task-1"),
    }

    validate_pilot_execution_authorization(
        protocol,
        report,
        experiment_id="pilot-001",
        task=task,
        now_ms=now_ms,
    )
    with pytest.raises(PilotReadinessError, match="stale"):
        validate_pilot_execution_authorization(
            protocol,
            report,
            experiment_id="pilot-001",
            task=task,
            now_ms=now_ms + 1_000,
        )
    with pytest.raises(PilotReadinessError, match="task family"):
        validate_pilot_execution_authorization(
            protocol,
            report,
            experiment_id="pilot-001",
            task={**task, "task_family": "unity"},
            now_ms=now_ms,
        )


def test_hash_matched_but_unsigned_receipts_cannot_authorize_a_pilot():
    protocol = freeze_pilot_protocol(_protocol_payload())
    pins, blobs = _receipts(protocol.protocol_hash, signed=False)

    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
    )

    assert report.ready is False
    assert {
        finding.code for finding in report.findings
    } >= {"invalid_runtime_codex_receipt"}


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda payload: payload["task_ids"].append("task-1"),
            "unique task IDs",
        ),
        (
            lambda payload: payload["confirmation_task_ids"].append("task-1"),
            "pilot task overlap",
        ),
        (
            lambda payload: (
                payload["confirmation_task_identities"].update(
                    {
                        "confirm-1": {
                            **_task_identity("PILOT-TASK-1"),
                            "repo": (
                                "git@github.com:"
                                "unity-technologies/example"
                            ),
                            "canonical_repo_id": (
                                "git@github.com:"
                                "unity-technologies/example"
                            ),
                        }
                    }
                )
            ),
            "canonical task overlap",
        ),
        (
            lambda payload: payload["arm_budgets"]["C"].update(
                {"max_cost_usd": 3.0}
            ),
            "identical ex-ante",
        ),
        (
            lambda payload: payload.update({"target_power": 0.80}),
            "target_power",
        ),
        (
            lambda payload: payload.update(
                {"alternative_b_win_rate": 0.99}
            ),
            "alternative_b_win_rate",
        ),
        (
            lambda payload: payload["stop_rule"].update(
                {"outcome_dependent": True}
            ),
            "must not depend on outcomes",
        ),
        (
            lambda payload: payload.update({"observed_effect": 0.2}),
            "outcome-dependent fields",
        ),
    ],
)
def test_protocol_rejects_posthoc_or_invalid_designs(mutate, match):
    payload = _protocol_payload()
    mutate(payload)

    with pytest.raises(PilotReadinessError, match=match):
        freeze_pilot_protocol(payload)


def test_readiness_rejects_hermetic_tracer_dirty_tree_and_bad_digest():
    protocol = freeze_pilot_protocol(_protocol_payload())
    pins, blobs = _receipts(protocol.protocol_hash)
    worktree = json.loads(blobs["receipt://worktree"])
    worktree["clean"] = False
    worktree["status_porcelain"] = " M supervisor/example.py"
    blobs["receipt://worktree"] = _receipt_bytes(worktree)
    pins["worktree"] = _signed_pin(
        "worktree",
        blobs["receipt://worktree"],
        protocol.protocol_hash,
    )
    tracer = json.loads(blobs["receipt://operational_tracer"])
    tracer["mode"] = "hermetic"
    tracer["external_provider_calls"] = False
    blobs["receipt://operational_tracer"] = _receipt_bytes(tracer)
    pins["operational_tracer"] = _signed_pin(
        "operational_tracer",
        blobs["receipt://operational_tracer"],
        protocol.protocol_hash,
    )
    pins["runtime_codex"] = EvidencePin(
        ref=pins["runtime_codex"].ref,
        sha256="c" * 64,
    )

    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )

    assert report.ready is False
    codes = {finding.code for finding in report.findings}
    assert "execution_tree_not_clean" in codes
    assert "operational_tracer_mode_invalid" in codes
    assert "operational_tracer_is_hermetic_only" in codes
    assert "invalid_runtime_codex_receipt" in codes


def test_runtime_and_verifier_receipts_must_equal_frozen_protocol_pins():
    protocol = freeze_pilot_protocol(_protocol_payload())
    pins, blobs = _receipts(protocol.protocol_hash)
    runtime = json.loads(blobs["receipt://runtime_codex"])
    runtime["runtime_implementation_sha256"] = "c" * 64
    blobs["receipt://runtime_codex"] = _receipt_bytes(runtime)
    pins["runtime_codex"] = _signed_pin(
        "runtime_codex",
        blobs["receipt://runtime_codex"],
        protocol.protocol_hash,
    )
    verifier = json.loads(blobs["receipt://verifier_unity"])
    verifier["verifier_implementation_sha256"] = "d" * 64
    blobs["receipt://verifier_unity"] = _receipt_bytes(verifier)
    pins["verifier_unity"] = _signed_pin(
        "verifier_unity",
        blobs["receipt://verifier_unity"],
        protocol.protocol_hash,
    )

    report = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )

    codes = {finding.code for finding in report.findings}
    assert "runtime_codex_protocol_pin_mismatch" in codes
    assert "verifier_unity_protocol_pin_mismatch" in codes


def test_readiness_report_is_deterministic_and_missing_receipts_fail_closed():
    protocol = freeze_pilot_protocol(deepcopy(_protocol_payload()))
    pins, blobs = _receipts(protocol.protocol_hash)
    pins.pop("budget_authorization")

    first = validate_pilot_readiness(
        protocol,
        receipts=pins,
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )
    second = validate_pilot_readiness(
        protocol,
        receipts=dict(reversed(list(pins.items()))),
        evidence_resolver=blobs.get,
        trusted_receipt_verifiers=TRUSTED_RECEIPT_VERIFIERS,
    )

    assert first.ready is False
    assert first.report_hash == second.report_hash
    assert [finding.code for finding in first.findings] == [
        "missing_budget_authorization_receipt"
    ]
