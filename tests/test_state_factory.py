from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
from typing import Any, Mapping

import pytest

from supervisor.config import Config
from supervisor.evidence_ledger import canonical_json_bytes
from supervisor.ledger_checkpoints import normalize_checkpoint_identity
from supervisor.state_factory import (
    CheckpointRuntime,
    DAEMON_REQUIRED_STATE_METHODS,
    StateFactoryError,
    build_state,
    require_state_capabilities,
)


class _ExternalTestAuthority:
    key_id = "external-test-key"
    algorithm = "hmac-sha256"

    def __init__(self) -> None:
        self._key = b"test-only-key-not-returned-to-supervisor"

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        expected = base64.b64encode(
            hmac.new(self._key, payload, hashlib.sha256).digest()
        ).decode("ascii")
        return (
            signature.get("key_id") == self.key_id
            and signature.get("algorithm") == self.algorithm
            and hmac.compare_digest(
                str(signature.get("signature") or ""),
                expected,
            )
        )


class _ExternalPins:
    def __init__(self) -> None:
        self._values: dict[bytes, dict[str, Any]] = {}
        self._latest: dict[str, dict[str, Any]] = {}

    def pin(self, identity: Mapping[str, Any]) -> None:
        normalized = normalize_checkpoint_identity(identity)
        encoded = canonical_json_bytes(normalized)
        current = self._latest.get(str(normalized["run_id"]))
        if current is not None and int(current["event_count"]) > int(
            normalized["event_count"]
        ):
            raise RuntimeError("rollback")
        self._values[encoded] = dict(normalized)
        self._latest[str(normalized["run_id"])] = dict(normalized)

    def get(
        self,
        identity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        value = self._values.get(
            canonical_json_bytes(normalize_checkpoint_identity(identity))
        )
        return None if value is None else dict(value)

    def latest(self, run_id: str) -> Mapping[str, Any] | None:
        value = self._latest.get(str(run_id))
        return None if value is None else dict(value)


def _config(tmp_path: Path, *, mode: str) -> Config:
    payload = {
        "orchestrator": {
            "run_registry_dir": str(tmp_path / "runs"),
        },
        "supervisor": {
            "state_db": str(tmp_path / "state.db"),
            "ledger_checkpoints": {
                "mode": mode,
                "max_events_between_checkpoints": 1,
                "checkpoint_store_path": str(tmp_path / "checkpoints"),
                "runtime_provider": (
                    "tests.fake:provider"
                    if mode == "authoritative"
                    else ""
                ),
            },
        },
        "models": {
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {
            "bot_token": "",
            "chat_id": "",
        },
    }
    return Config(**payload)


def test_state_factory_defaults_to_explicit_diagnostic_only(tmp_path):
    state = build_state(_config(tmp_path, mode="diagnostic_only"))
    assert state.event_ledger_assurance == "diagnostic-only"
    assert "commit_decision_verdict" in DAEMON_REQUIRED_STATE_METHODS
    require_state_capabilities(
        state,
        required_methods=DAEMON_REQUIRED_STATE_METHODS,
        profile="daemon observability and control",
    )


def test_state_factory_rejects_partial_backend_before_daemon_startup():
    class _PartialPostgresState:
        def write_event(self):
            return None

    with pytest.raises(
        StateFactoryError,
        match=(
            "does not support daemon observability and control: "
            "missing .*register_run"
        ),
    ):
        require_state_capabilities(
            _PartialPostgresState(),
            required_methods=DAEMON_REQUIRED_STATE_METHODS,
            profile="daemon observability and control",
        )


def test_build_state_applies_capability_guard_at_composition(tmp_path):
    state = build_state(
        _config(tmp_path, mode="diagnostic_only"),
        required_capabilities=DAEMON_REQUIRED_STATE_METHODS,
        capability_profile="daemon observability and control",
    )
    assert state.event_ledger_assurance == "diagnostic-only"

    with pytest.raises(
        StateFactoryError,
        match=(
            "does not support daemon observability and control: "
            "missing .*not_a_real_state_method"
        ),
    ):
        build_state(
            _config(tmp_path, mode="diagnostic_only"),
            required_capabilities=frozenset(
                {"not_a_real_state_method", "write_event"}
            ),
            capability_profile="daemon observability and control",
        )


def test_state_factory_composes_authoritative_runtime(tmp_path):
    authority = _ExternalTestAuthority()
    pins = _ExternalPins()

    def resolve(_provider_ref, _settings):
        return CheckpointRuntime(
            signer=authority,
            verifier=authority,
            trusted_pin_store=pins,
            provider_id="external-test-service",
            externally_managed=True,
            rollback_independent=True,
        )

    state = build_state(
        _config(tmp_path, mode="authoritative"),
        checkpoint_runtime_resolver=resolve,
    )
    assert state.event_ledger_assurance == "authoritative"
    state.write_event(
        run_id="factory-run",
        source="test",
        kind="run.completed",
        payload={"status": "completed"},
    )
    verification = state.verify_event_ledger("factory-run")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True
    identity = pins.latest("factory-run")
    assert identity is not None
    assert identity["signer_provider_id"] == "external-test-service"
    assert identity["signer_key_id"] == authority.key_id


def test_state_factory_fails_closed_when_runtime_resolution_fails(tmp_path):
    def fail(_provider_ref, _settings):
        raise RuntimeError("external service unavailable")

    with pytest.raises(
        StateFactoryError,
        match="could not be resolved",
    ):
        build_state(
            _config(tmp_path, mode="authoritative"),
            checkpoint_runtime_resolver=fail,
        )


def test_state_factory_rejects_noncanonical_runtime_provider_identity(tmp_path):
    authority = _ExternalTestAuthority()

    def resolve(_provider_ref, _settings):
        return CheckpointRuntime(
            signer=authority,
            verifier=authority,
            trusted_pin_store=_ExternalPins(),
            provider_id=" external-test-service ",
            externally_managed=True,
            rollback_independent=True,
        )

    with pytest.raises(
        StateFactoryError,
        match="provider_id must be canonical",
    ):
        build_state(
            _config(tmp_path, mode="authoritative"),
            checkpoint_runtime_resolver=resolve,
        )


def test_authoritative_config_requires_provider(tmp_path):
    payload = _config(tmp_path, mode="diagnostic_only").model_dump()
    payload["supervisor"]["ledger_checkpoints"] = {
        "mode": "authoritative",
        "runtime_provider": "",
    }
    with pytest.raises(
        ValueError,
        match="require runtime_provider",
    ):
        Config(**payload)


def test_stdio_mcp_main_uses_trusted_state_composition_root(
    monkeypatch,
    tmp_path,
):
    from mcp_tools import codex_supervisor_stdio as stdio

    cfg = _config(tmp_path, mode="diagnostic_only")
    composed_state = object()
    observed = {}

    class _Server:
        def run(self, *, transport):
            observed["transport"] = transport

    monkeypatch.setattr(stdio.Config, "load", lambda _path: cfg)
    monkeypatch.setattr(
        stdio,
        "build_state",
        lambda loaded: (
            observed.setdefault("cfg", loaded)
            and composed_state
        ),
    )
    monkeypatch.setattr(
        stdio,
        "build_codex_supervisor_mcp_server",
        lambda loaded, state: (
            observed.update({"server_cfg": loaded, "state": state})
            or _Server()
        ),
    )

    stdio.main(["--config", str(tmp_path / "config.yaml")])

    assert observed["cfg"] is cfg
    assert observed["server_cfg"] is cfg
    assert observed["state"] is composed_state
    assert observed["transport"] == "stdio"
