from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import yaml

from scripts import run_projection_registry_proofs


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "program"
    / "harness-v1"
    / "projection-registry.yaml"
)
_REGISTRY_SCHEMA_VERSION = "harness-evidence-projection-registry/v1"
_REGISTRY_PROOF_NODE_ID = (
    "tests/test_projection_registry.py::"
    "test_projection_registry_limits_and_binds_the_rebuildability_claim"
)
_TRACER_SCHEMA_VERSION = "harness-tracer-projection/v1"
_TRACER_SOURCE_EVENTS = [
    "tracer.submitted",
    "tracer.matrix.frozen",
    "tracer.assignment.persisted",
    "tracer.execution.joined",
    "tracer.trace.closed",
    "tracer.claim.authorized",
    "tracer.completed",
]
_QUALITY_SCHEMA_VERSION = "quality-trend-projection/v1"
_QUALITY_SOURCE_EVENTS = ["supervisor_quality_trend_projection"]
_QUALITY_AUTHORITY_INVENTORY = {
    "type": "external_exact_stream_checkpoint_pins",
    "parameter": "expected_stream_checkpoint_pins",
    "scope": "projection_bearing_run_ids_only",
    "empty_inventory_allowed": False,
}
_QUALITY_MAINTENANCE_CONCURRENCY = {
    "sqlite": "begin_immediate",
    "postgres": "writer_ordered_table_locks",
}


def _valid_projection(**overrides):
    projection = {
        "id": "example",
        "schema_version": _TRACER_SCHEMA_VERSION,
        "source_events": list(_TRACER_SOURCE_EVENTS),
        "status": "verified",
        "reducer": "supervisor.trace_graph:canonical_revision_hash",
        "proof": ["tests/test_example.py::test_exact"],
    }
    projection.update(overrides)
    return projection


def _valid_quality_projection(**overrides):
    projection = _valid_projection(
        schema_version=_QUALITY_SCHEMA_VERSION,
        source_events=list(_QUALITY_SOURCE_EVENTS),
        authority_inventory=dict(_QUALITY_AUTHORITY_INVENTORY),
        maintenance_concurrency=dict(_QUALITY_MAINTENANCE_CONCURRENCY),
    )
    projection.update(overrides)
    return projection


def _valid_registry(projections):
    return {
        "schema_version": _REGISTRY_SCHEMA_VERSION,
        "registry_proof": [_REGISTRY_PROOF_NODE_ID],
        "projections": projections,
    }


def _resolve_symbol(reference: str):
    module_name, qualified_name = reference.split(":", 1)
    value = importlib.import_module(module_name)
    for segment in qualified_name.split("."):
        value = getattr(value, segment)
    return value


def test_projection_proof_attribution_fixture_calls_registered_binding() -> None:
    from supervisor.trace_graph import canonical_revision_hash

    assert canonical_revision_hash("projection-proof") != ""


def test_projection_proof_attribution_fixture_does_not_call_binding() -> None:
    assert True


def test_projection_registry_limits_and_binds_the_rebuildability_claim() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert registry["schema_version"] == "harness-evidence-projection-registry/v1"
    assert registry["scope"] == "evidence_authoritative_only"
    assert "Unlisted materialized tables are not covered" in registry["claim"]
    assert registry["verification_command"] == (
        "make test-projection-registry"
    )
    assert registry["registry_proof"] == [
        "tests/test_projection_registry.py::"
        "test_projection_registry_limits_and_binds_the_rebuildability_claim"
    ]
    assert [projection["id"] for projection in registry["projections"]] == [
        "supervisor-quality-trends",
        "harness-tracer",
    ]
    assert {
        key: registry["projections"][0][key]
        for key in ("reducer", "sqlite_rebuilder", "postgres_rebuilder")
    } == {
        "reducer": (
            "supervisor.quality_projection:"
            "rebuild_quality_trend_projection"
        ),
        "sqlite_rebuilder": (
            "supervisor.state:"
            "State.rebuild_quality_trend_projection_from_ledger"
        ),
        "postgres_rebuilder": (
            "supervisor.postgres_state:"
            "PostgresState.rebuild_quality_trend_projection_from_ledger"
        ),
    }
    assert {
        key: registry["projections"][1][key]
        for key in ("reducer", "initial")
    } == {
        "reducer": (
            "supervisor.evidence_committer:"
            "reduce_tracer_evidence_projection"
        ),
        "initial": (
            "supervisor.evidence_committer:"
            "initial_tracer_evidence_projection"
        ),
    }

    for projection in registry["projections"]:
        assert projection["status"] == "verified"
        assert projection["source_events"]
        assert callable(_resolve_symbol(projection["reducer"]))
        if "initial" in projection:
            assert callable(_resolve_symbol(projection["initial"]))
        for backend in ("sqlite_rebuilder", "postgres_rebuilder"):
            if backend in projection:
                assert callable(_resolve_symbol(projection[backend]))
        if "sqlite_rebuilder" in projection:
                assert projection["authority_inventory"] == {
                    "type": "external_exact_stream_checkpoint_pins",
                    "parameter": "expected_stream_checkpoint_pins",
                    "scope": "projection_bearing_run_ids_only",
                    "empty_inventory_allowed": False,
                }
                assert projection["maintenance_concurrency"] == {
                    "sqlite": "begin_immediate",
                    "postgres": "writer_ordered_table_locks",
                }
        for proof in projection["proof"]:
            path_text, test_name = proof.split("::", 1)
            path = REPOSITORY_ROOT / path_text
            assert path.is_file()
            assert f"def {test_name}(" in path.read_text(encoding="utf-8")

    tracer = registry["projections"][1]
    assert tracer["persistence_semantics"] == (
        "post_execution_stage_projection"
    )
    assert tracer["pre_execution_attested"] is False
    assert registry["excluded_scope"]


def test_projection_registry_runner_rejects_duplicate_proof_node_ids(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        yaml.safe_dump(
            _valid_registry(
                [
                    _valid_projection(id="first"),
                    _valid_projection(id="second"),
                ]
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="duplicate proof node id"):
        run_projection_registry_proofs._load_proofs(registry)


def test_projection_registry_runner_rejects_empty_registry(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        yaml.safe_dump(_valid_registry([])),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="at least one projection"):
        run_projection_registry_proofs._load_proofs(registry)


@pytest.mark.parametrize(
    "schema_version",
    [
        "fabricated-projection-registry/v999",
        ["harness-evidence-projection-registry/v1"],
    ],
)
def test_projection_registry_runner_rejects_unsupported_registry_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    registry = tmp_path / "registry.yaml"
    payload = _valid_registry([_valid_projection()])
    payload["schema_version"] = schema_version
    registry.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="unsupported registry schema version"):
        run_projection_registry_proofs._load_proofs(registry)


def test_projection_registry_runner_requires_the_registry_proof(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.yaml"
    payload = _valid_registry([_valid_projection()])
    payload.pop("registry_proof")
    registry.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="required registry proof"):
        run_projection_registry_proofs._load_proofs(registry)


@pytest.mark.parametrize(
    "schema_version",
    [
        "fabricated-projection/v999",
        ["harness-tracer-projection/v1"],
    ],
)
def test_projection_registry_runner_rejects_unsupported_projection_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    registry = tmp_path / "registry.yaml"
    projection = _valid_projection(
        schema_version=schema_version,
    )
    registry.write_text(
        yaml.safe_dump(_valid_registry([projection])),
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match="unsupported projection schema version",
    ):
        run_projection_registry_proofs._load_proofs(registry)


@pytest.mark.parametrize(
    "source_events",
    [
        [],
        ["fabricated.event"],
    ],
)
def test_projection_registry_runner_requires_exact_source_events(
    tmp_path: Path,
    source_events: list[str],
) -> None:
    registry = tmp_path / "registry.yaml"
    projection = _valid_projection(source_events=source_events)
    registry.write_text(
        yaml.safe_dump(_valid_registry([projection])),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="non-empty exact source_events"):
        run_projection_registry_proofs._load_proofs(registry)


@pytest.mark.parametrize(
    ("field", "fabricated_value"),
    [
        (
            "authority_inventory",
            {
                "type": "trust_me",
                "parameter": "anything",
                "scope": "everything",
                "empty_inventory_allowed": True,
            },
        ),
        (
            "maintenance_concurrency",
            {
                "sqlite": "unlocked",
                "postgres": "best_effort",
            },
        ),
    ],
)
def test_projection_registry_runner_rejects_unrecognized_projection_metadata(
    tmp_path: Path,
    field: str,
    fabricated_value: dict[str, object],
) -> None:
    registry = tmp_path / "registry.yaml"
    projection = _valid_quality_projection(
        **{field: fabricated_value},
    )
    registry.write_text(
        yaml.safe_dump(_valid_registry([projection])),
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match=f"unrecognized {field} metadata",
    ):
        run_projection_registry_proofs._load_proofs(registry)


def test_projection_registry_runner_rejects_unresolvable_symbol(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        yaml.safe_dump(
            _valid_registry(
                [
                    _valid_projection(
                        id="invalid",
                        reducer="not_a_module:not_a_symbol",
                    )
                ]
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="could not be resolved"):
        run_projection_registry_proofs._load_proofs(registry)


def test_projection_registry_runner_rejects_skipped_exact_proof(
    tmp_path: Path,
) -> None:
    node_id = "tests/test_example.py::test_exact"
    receipt = tmp_path / "proof-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    run_projection_registry_proofs
                    .PROOF_RECEIPT_SCHEMA_VERSION
                ),
                "exit_status": 0,
                "collected_node_ids": [node_id],
                "reports": [
                    {
                        "nodeid": node_id,
                        "when": "setup",
                        "outcome": "skipped",
                    }
                ],
                "binding_counts_by_node_id": {node_id: {}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="did not pass exactly once"):
        run_projection_registry_proofs._validate_proof_receipt(
            receipt,
            expected_node_ids=[node_id],
            expected_binding_proofs={},
        )


def test_projection_registry_runner_rejects_unexercised_binding(
    tmp_path: Path,
) -> None:
    node_id = "tests/test_example.py::test_exact"
    binding = "supervisor.trace_graph:canonical_revision_hash"
    receipt = tmp_path / "proof-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    run_projection_registry_proofs
                    .PROOF_RECEIPT_SCHEMA_VERSION
                ),
                "exit_status": 0,
                "collected_node_ids": [node_id],
                "reports": [
                    {
                        "nodeid": node_id,
                        "when": "call",
                        "outcome": "passed",
                    }
                ],
                "binding_counts_by_node_id": {
                    node_id: {binding: 0},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="did not exercise"):
        run_projection_registry_proofs._validate_proof_receipt(
            receipt,
            expected_node_ids=[node_id],
            expected_binding_proofs={
                "example-projection": {
                    "node_ids": [node_id],
                    "bindings": [binding],
                }
            },
        )


def test_projection_registry_runner_rejects_binding_from_unrelated_proof(
    tmp_path: Path,
) -> None:
    registered_node = "tests/test_example.py::test_registered_projection"
    unrelated_node = "tests/test_example.py::test_unrelated_projection"
    binding = "supervisor.trace_graph:canonical_revision_hash"
    receipt = tmp_path / "proof-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    run_projection_registry_proofs
                    .PROOF_RECEIPT_SCHEMA_VERSION
                ),
                "exit_status": 0,
                "collected_node_ids": [registered_node, unrelated_node],
                "reports": [
                    {
                        "nodeid": registered_node,
                        "when": "call",
                        "outcome": "passed",
                    },
                    {
                        "nodeid": unrelated_node,
                        "when": "call",
                        "outcome": "passed",
                    },
                ],
                "binding_counts_by_node_id": {
                    registered_node: {binding: 0},
                    unrelated_node: {binding: 1},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="exact proof nodes"):
        run_projection_registry_proofs._validate_proof_receipt(
            receipt,
            expected_node_ids=[registered_node, unrelated_node],
            expected_binding_proofs={
                "registered-projection": {
                    "node_ids": [registered_node],
                    "bindings": [binding],
                }
            },
        )


def test_projection_proof_plugin_does_not_credit_an_unrelated_node() -> None:
    calling_node = (
        "tests/test_projection_registry.py::"
        "test_projection_proof_attribution_fixture_calls_registered_binding"
    )
    unrelated_node = (
        "tests/test_projection_registry.py::"
        "test_projection_proof_attribution_fixture_does_not_call_binding"
    )
    binding = "supervisor.trace_graph:canonical_revision_hash"

    with pytest.raises(SystemExit, match="exact proof nodes"):
        run_projection_registry_proofs._run_pytest_proofs(
            [calling_node, unrelated_node],
            bindings=[binding],
            expected_binding_proofs={
                "unrelated-projection": {
                    "node_ids": [unrelated_node],
                    "bindings": [binding],
                }
            },
        )


def test_projection_registry_runner_sanitizes_injected_pytest_plugins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_file = tmp_path / "test_skipped.py"
    test_file.write_text(
        "import pytest\n"
        "@pytest.mark.skip(reason='must remain skipped')\n"
        "def test_skipped():\n"
        "    raise AssertionError('must not run')\n",
        encoding="utf-8",
    )
    (tmp_path / "attacker_plugin.py").write_text(
        "raise RuntimeError('injected pytest plugin loaded')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("PYTEST_PLUGINS", "attacker_plugin")

    with pytest.raises(SystemExit):
        run_projection_registry_proofs._run_exact_hermetic_proof(
            f"{test_file}::test_skipped"
        )


def test_projection_registry_runner_requires_registered_postgres_node(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "postgres-manifest.txt"
    manifest.write_text(
        "tests/test_postgres_ledger_lane.py::test_registered\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="missing from the exact manifest"):
        run_projection_registry_proofs._validate_postgres_proofs(
            ["tests/test_postgres_ledger_lane.py::test_unregistered"],
            manifest_path=manifest,
        )


def test_projection_registry_runner_requires_exact_postgres_proof_for_rebuilder(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        yaml.safe_dump(
            _valid_registry(
                [
                    _valid_quality_projection(
                        id="postgres-without-postgres-proof",
                        postgres_rebuilder=(
                            "supervisor.trace_graph:"
                            "canonical_revision_hash"
                        ),
                    )
                ]
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        SystemExit,
        match="postgres_rebuilder requires an exact PostgreSQL proof",
    ):
        run_projection_registry_proofs._load_proofs(registry)
