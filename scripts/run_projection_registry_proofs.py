#!/usr/bin/env python3
"""Execute every proof named by the Harness v1 projection registry."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/program/harness-v1/projection-registry.yaml"
POSTGRES_TEST_FILE = "tests/test_postgres_ledger_lane.py"
POSTGRES_MANIFEST = ROOT / "tests/postgres_conformance_manifest.txt"
PROOF_RECEIPT_SCHEMA_VERSION = "harness-projection-proof-receipt/v2"
SUPPORTED_REGISTRY_SCHEMA_VERSIONS = frozenset(
    {"harness-evidence-projection-registry/v1"}
)
REQUIRED_REGISTRY_PROOF_NODE_ID = (
    "tests/test_projection_registry.py::"
    "test_projection_registry_limits_and_binds_the_rebuildability_claim"
)
PROJECTION_SCHEMA_SOURCE_EVENTS = {
    "harness-tracer-projection/v1": (
        "tracer.submitted",
        "tracer.matrix.frozen",
        "tracer.assignment.persisted",
        "tracer.execution.joined",
        "tracer.trace.closed",
        "tracer.claim.authorized",
        "tracer.completed",
    ),
    "quality-trend-projection/v1": (
        "supervisor_quality_trend_projection",
    ),
}
PROJECTION_SCHEMA_METADATA = {
    "harness-tracer-projection/v1": {
        "authority_inventory": None,
        "maintenance_concurrency": None,
    },
    "quality-trend-projection/v1": {
        "authority_inventory": {
            "type": "external_exact_stream_checkpoint_pins",
            "parameter": "expected_stream_checkpoint_pins",
            "scope": "projection_bearing_run_ids_only",
            "empty_inventory_allowed": False,
        },
        "maintenance_concurrency": {
            "sqlite": "begin_immediate",
            "postgres": "writer_ordered_table_locks",
        },
    },
}
SUPPORTED_PROJECTION_SCHEMA_VERSIONS = frozenset(
    PROJECTION_SCHEMA_SOURCE_EVENTS
)
_SYMBOL_FIELDS = (
    "reducer",
    "initial",
    "sqlite_rebuilder",
    "postgres_rebuilder",
)
_PYTEST_ENV_BLOCKLIST = {
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTHONPATH",
}


@dataclass(frozen=True)
class _RegistryPlan:
    proof_node_ids: tuple[str, ...]
    hermetic_node_ids: tuple[str, ...]
    postgres_node_ids: tuple[str, ...]
    hermetic_binding_proofs: dict[str, dict[str, list[str]]]
    postgres_binding_proofs: dict[str, dict[str, list[str]]]


def _resolve_callable(reference: str) -> Any:
    try:
        module_name, qualified_name = reference.split(":", 1)
        value: Any = importlib.import_module(module_name)
        for segment in qualified_name.split("."):
            value = getattr(value, segment)
    except (AttributeError, ImportError, ValueError) as exc:
        raise SystemExit(
            "projection registry symbol could not be resolved: "
            f"{reference}"
        ) from exc
    if not callable(value):
        raise SystemExit(
            f"projection registry symbol is not callable: {reference}"
        )
    return value


def _required_symbol(
    projection: dict[str, Any],
    field: str,
) -> str:
    reference = projection.get(field)
    if not isinstance(reference, str) or ":" not in reference:
        raise SystemExit(
            f"projection registry {field} must be a symbol reference: "
            f"{projection.get('id')}"
        )
    _resolve_callable(reference)
    return reference


def _optional_symbol(
    projection: dict[str, Any],
    field: str,
) -> str | None:
    if field not in projection:
        return None
    return _required_symbol(projection, field)


def _deduplicated(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _binding_inventory(
    binding_proofs: dict[str, dict[str, list[str]]],
) -> list[str]:
    return list(
        _deduplicated(
            [
                binding
                for proof in binding_proofs.values()
                for binding in proof["bindings"]
            ]
        )
    )


def _load_registry_plan(registry_path: Path = REGISTRY) -> _RegistryPlan:
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("projection registry root must be a mapping")
    registry_schema_version = payload.get("schema_version")
    if (
        not isinstance(registry_schema_version, str)
        or registry_schema_version not in SUPPORTED_REGISTRY_SCHEMA_VERSIONS
    ):
        raise SystemExit(
            "projection registry has unsupported registry schema version: "
            f"{registry_schema_version!r}"
        )
    projections = payload.get("projections")
    if not isinstance(projections, list) or not projections:
        raise SystemExit(
            "projection registry must contain at least one projection"
        )
    registry_proofs = payload.get("registry_proof")
    if registry_proofs != [REQUIRED_REGISTRY_PROOF_NODE_ID]:
        raise SystemExit(
            "projection registry must contain the required registry proof: "
            f"{REQUIRED_REGISTRY_PROOF_NODE_ID}"
        )
    proofs: list[str] = []
    hermetic: list[str] = []
    postgres: list[str] = []
    hermetic_binding_proofs: dict[str, dict[str, list[str]]] = {}
    postgres_binding_proofs: dict[str, dict[str, list[str]]] = {}
    projection_ids: set[str] = set()

    for raw_node_id in registry_proofs:
        node_id = _exact_node_id(raw_node_id)
        proofs.append(node_id)
        hermetic.append(node_id)

    for projection in projections:
        if not isinstance(projection, dict):
            raise SystemExit("projection registry entries must be mappings")
        projection_id = str(projection.get("id") or "").strip()
        if not projection_id or projection_id in projection_ids:
            raise SystemExit(
                "projection registry ids must be unique and non-empty"
            )
        projection_ids.add(projection_id)
        if projection.get("status") != "verified":
            raise SystemExit(
                "projection registry contains a non-verified projection: "
                f"{projection_id}"
            )
        projection_schema_version = projection.get("schema_version")
        if (
            not isinstance(projection_schema_version, str)
            or projection_schema_version
            not in SUPPORTED_PROJECTION_SCHEMA_VERSIONS
        ):
            raise SystemExit(
                "projection registry has unsupported projection schema "
                f"version for {projection_id}: "
                f"{projection_schema_version!r}"
            )
        source_events = projection.get("source_events")
        expected_source_events = PROJECTION_SCHEMA_SOURCE_EVENTS[
            projection_schema_version
        ]
        if (
            not isinstance(source_events, list)
            or not source_events
            or tuple(source_events) != expected_source_events
        ):
            raise SystemExit(
                "projection registry requires non-empty exact source_events "
                f"for {projection_id} schema {projection_schema_version}"
            )
        expected_metadata = PROJECTION_SCHEMA_METADATA[
            projection_schema_version
        ]
        for field, expected_value in expected_metadata.items():
            if (
                expected_value is None
                and field in projection
            ) or (
                expected_value is not None
                and projection.get(field) != expected_value
            ):
                raise SystemExit(
                    "projection registry has unrecognized "
                    f"{field} metadata for {projection_id}"
                )
        reducer = _required_symbol(projection, "reducer")
        initial = _optional_symbol(projection, "initial")
        sqlite_rebuilder = _optional_symbol(
            projection,
            "sqlite_rebuilder",
        )
        postgres_rebuilder = _optional_symbol(
            projection,
            "postgres_rebuilder",
        )
        for field in _SYMBOL_FIELDS:
            if field in projection and not isinstance(
                projection.get(field),
                str,
            ):
                raise SystemExit(
                    f"projection registry {field} must be text: "
                    f"{projection_id}"
                )
        projection_proofs = projection.get("proof")
        if not isinstance(projection_proofs, list) or not projection_proofs:
            raise SystemExit(
                "projection registry entry has no executable proof: "
                f"{projection_id}"
            )
        projection_hermetic_node_ids: list[str] = []
        projection_postgres_node_ids: list[str] = []
        for raw_node_id in projection_proofs:
            node_id = _exact_node_id(raw_node_id)
            proofs.append(node_id)
            if node_id.startswith(POSTGRES_TEST_FILE + "::"):
                postgres.append(node_id)
                projection_postgres_node_ids.append(node_id)
            else:
                hermetic.append(node_id)
                projection_hermetic_node_ids.append(node_id)
        has_postgres_proof = bool(projection_postgres_node_ids)
        if postgres_rebuilder is not None and not has_postgres_proof:
            raise SystemExit(
                "projection registry postgres_rebuilder requires an exact "
                f"PostgreSQL proof: {projection_id}"
            )
        if projection_hermetic_node_ids:
            projection_bindings = [reducer]
            if initial is not None:
                projection_bindings.append(initial)
            if sqlite_rebuilder is not None:
                projection_bindings.append(sqlite_rebuilder)
            hermetic_binding_proofs[projection_id] = {
                "node_ids": projection_hermetic_node_ids,
                "bindings": list(_deduplicated(projection_bindings)),
            }
        if has_postgres_proof:
            if postgres_rebuilder is None:
                raise SystemExit(
                    "PostgreSQL projection proofs require postgres_rebuilder: "
                    f"{projection_id}"
                )
            postgres_binding_proofs[projection_id] = {
                "node_ids": projection_postgres_node_ids,
                "bindings": list(
                    _deduplicated([reducer, postgres_rebuilder])
                ),
            }
    seen: set[str] = set()
    duplicates: list[str] = []
    for node_id in proofs:
        if node_id in seen:
            duplicates.append(node_id)
        seen.add(node_id)
    if duplicates:
        raise SystemExit(
            "projection registry contains a duplicate proof node id: "
            + ", ".join(sorted(set(duplicates)))
        )
    if not proofs:
        raise SystemExit(
            "projection registry must contain at least one proof node id"
        )
    return _RegistryPlan(
        proof_node_ids=tuple(proofs),
        hermetic_node_ids=tuple(hermetic),
        postgres_node_ids=tuple(postgres),
        hermetic_binding_proofs=hermetic_binding_proofs,
        postgres_binding_proofs=postgres_binding_proofs,
    )


def _exact_node_id(raw_node_id: Any) -> str:
    if not isinstance(raw_node_id, str) or "::" not in raw_node_id:
        raise SystemExit(
            "projection registry proof must be an exact pytest node id: "
            f"{raw_node_id!r}"
        )
    node_id = raw_node_id.strip()
    if not node_id:
        raise SystemExit(
            "projection registry proof node id must be non-empty"
        )
    return node_id


def _load_proofs(registry_path: Path = REGISTRY) -> list[str]:
    return list(_load_registry_plan(registry_path).proof_node_ids)


def _sanitized_pytest_env(
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _PYTEST_ENV_BLOCKLIST
    }
    environment.update(
        {
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if extra:
        environment.update(extra)
    return environment


def _validate_proof_receipt(
    receipt_path: Path,
    *,
    expected_node_ids: list[str],
    expected_binding_proofs: dict[str, dict[str, list[str]]],
) -> None:
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PROOF_RECEIPT_SCHEMA_VERSION
        or payload.get("exit_status") != 0
    ):
        raise SystemExit("projection proof receipt is invalid")
    collected = payload.get("collected_node_ids")
    if (
        not isinstance(collected, list)
        or len(collected) != len(expected_node_ids)
        or set(collected) != set(expected_node_ids)
    ):
        raise SystemExit(
            "projection proof collection differs from the exact registry"
        )
    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise SystemExit("projection proof receipt lacks test reports")
    for node_id in expected_node_ids:
        node_reports = [
            report
            for report in reports
            if isinstance(report, dict)
            and report.get("nodeid") == node_id
        ]
        call_passes = [
            report
            for report in node_reports
            if report.get("when") == "call"
            and report.get("outcome") == "passed"
        ]
        bad_outcomes = [
            report
            for report in node_reports
            if report.get("outcome") in {"failed", "skipped"}
        ]
        if len(call_passes) != 1 or bad_outcomes:
            raise SystemExit(
                f"projection proof {node_id} did not pass exactly once"
            )
    expected_bindings = list(
        dict.fromkeys(
            binding
            for proof in expected_binding_proofs.values()
            for binding in proof.get("bindings", [])
        )
    )
    binding_counts_by_node_id = payload.get(
        "binding_counts_by_node_id"
    )
    if (
        not isinstance(binding_counts_by_node_id, dict)
        or set(binding_counts_by_node_id) != set(expected_node_ids)
    ):
        raise SystemExit(
            "projection proof receipt binding attribution differs from "
            "the exact proof nodes"
        )
    for node_id in expected_node_ids:
        node_counts = binding_counts_by_node_id.get(node_id)
        if (
            not isinstance(node_counts, dict)
            or set(node_counts) != set(expected_bindings)
            or any(
                not isinstance(count, int) or count < 0
                for count in node_counts.values()
            )
        ):
            raise SystemExit(
                "projection proof receipt binding attribution differs from "
                "the exact proof nodes"
            )
    for projection_id, proof in expected_binding_proofs.items():
        proof_node_ids = proof.get("node_ids")
        proof_bindings = proof.get("bindings")
        if (
            not isinstance(proof_node_ids, list)
            or not proof_node_ids
            or not set(proof_node_ids).issubset(expected_node_ids)
            or not isinstance(proof_bindings, list)
            or not proof_bindings
        ):
            raise SystemExit(
                "projection proof binding requirement is invalid: "
                f"{projection_id}"
            )
        unexercised = [
            binding
            for binding in proof_bindings
            if sum(
                binding_counts_by_node_id[node_id][binding]
                for node_id in proof_node_ids
            )
            <= 0
        ]
        if unexercised:
            raise SystemExit(
                "projection proof did not exercise registered "
                "implementation in its exact proof nodes: "
                f"{projection_id}: "
                + ", ".join(unexercised)
            )


def _run_pytest_proofs(
    node_ids: list[str],
    *,
    bindings: list[str],
    expected_binding_proofs: dict[str, dict[str, list[str]]],
) -> None:
    if not node_ids:
        return
    with tempfile.TemporaryDirectory(
        prefix="codex-projection-proof-"
    ) as temp_dir:
        receipt_path = Path(temp_dir) / "proof-receipt.json"
        subprocess.run(
            [
                "uv",
                "run",
                "--extra",
                "dev",
                "python",
                "-m",
                "pytest",
                "-q",
                "-o",
                "addopts=",
                "-p",
                "pytest_asyncio.plugin",
                "-p",
                "scripts.projection_proof_plugin",
                *node_ids,
            ],
            cwd=ROOT,
            env=_sanitized_pytest_env(
                {
                    "CODEX_PROJECTION_PROOF_BINDINGS": json.dumps(bindings),
                    "CODEX_PROJECTION_PROOF_RECEIPT": str(receipt_path),
                }
            ),
            check=True,
        )
        _validate_proof_receipt(
            receipt_path,
            expected_node_ids=node_ids,
            expected_binding_proofs=expected_binding_proofs,
        )


def _run_exact_hermetic_proof(node_id: str) -> None:
    _run_pytest_proofs(
        [node_id],
        bindings=[],
        expected_binding_proofs={},
    )


def _validate_postgres_proofs(
    node_ids: list[str],
    *,
    manifest_path: Path = POSTGRES_MANIFEST,
) -> None:
    manifest = [
        line.strip()
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(manifest) != len(set(manifest)):
        raise SystemExit("PostgreSQL conformance manifest contains duplicates")
    missing = [node_id for node_id in node_ids if node_id not in manifest]
    if missing:
        raise SystemExit(
            "registered PostgreSQL projection proof is missing from the exact "
            "manifest: "
            + ", ".join(missing)
        )


def _postgres_manifest_node_ids(
    manifest_path: Path = POSTGRES_MANIFEST,
) -> list[str]:
    return [
        line.strip()
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_postgres_proofs(
    node_ids: list[str],
    *,
    bindings: list[str],
    expected_binding_proofs: dict[str, dict[str, list[str]]],
) -> None:
    if not node_ids:
        return
    _validate_postgres_proofs(node_ids)
    with tempfile.TemporaryDirectory(
        prefix="codex-postgres-projection-proof-"
    ) as temp_dir:
        receipt_path = Path(temp_dir) / "proof-receipt.json"
        subprocess.run(
            ["./scripts/run_postgres_conformance.sh"],
            cwd=ROOT,
            env=_sanitized_pytest_env(
                {
                    "CODEX_PROJECTION_PROOF_BINDINGS": json.dumps(bindings),
                    "CODEX_PROJECTION_PROOF_RECEIPT": str(receipt_path),
                }
            ),
            check=True,
        )
        _validate_proof_receipt(
            receipt_path,
            expected_node_ids=_postgres_manifest_node_ids(),
            expected_binding_proofs=expected_binding_proofs,
        )


def main() -> int:
    plan = _load_registry_plan()
    _run_pytest_proofs(
        list(plan.hermetic_node_ids),
        bindings=_binding_inventory(plan.hermetic_binding_proofs),
        expected_binding_proofs=plan.hermetic_binding_proofs,
    )
    _run_postgres_proofs(
        list(plan.postgres_node_ids),
        bindings=_binding_inventory(plan.postgres_binding_proofs),
        expected_binding_proofs=plan.postgres_binding_proofs,
    )
    print(
        f"Hermetic projection proofs: "
        f"{len(plan.hermetic_node_ids)} exact passes"
    )
    print(
        "PostgreSQL projection proofs: "
        f"{len(plan.postgres_node_ids)} exact manifest entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
