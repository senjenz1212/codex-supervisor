from __future__ import annotations

import base64
import copy
import hmac
import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Mapping

import pytest

import supervisor.backend_run_replay as backend_run_replay_module
import supervisor.swe_bench_official_oracle as official_oracle
from supervisor.backend_run_replay import (
    BACKEND_RUN_REPLAY_SCHEMA_VERSION,
    BackendRunReplayConflictError,
    BackendRunReplayGuardError,
    SQLiteBackendRunReplayGuard,
)
from supervisor.swe_bench_official_oracle import (
    SWE_BENCH_BOUND_ORACLE_RECEIPT_SCHEMA_VERSION,
    SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS,
    SweBenchVerifierExecutionSpec,
    build_swe_bench_execution_authority,
    run_task_spec_bound_official_harness_oracle,
)
from supervisor.task_environment import (
    FrozenTaskResult,
    SweBenchVerifier,
    TaskSpec,
    bind_frozen_result_to_task,
)


def _json_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


_AUTHORITY_SECRET = b"fixture-only-swe-bench-authority-key"
_TRUSTED_BACKEND_MANIFEST_HASH = sha256(
    b"tests.attested-swebench-backend/v1"
).hexdigest()


class _HmacAuthority:
    key_id = "tests.swe-bench-authority"
    algorithm = "hmac-sha256"

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(_AUTHORITY_SECRET, payload, sha256).digest()

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, str],
    ) -> bool:
        if (
            signature.get("key_id") != self.key_id
            or signature.get("algorithm") != self.algorithm
        ):
            return False
        try:
            observed = base64.b64decode(
                signature.get("signature", ""),
                validate=True,
            )
        except (ValueError, TypeError):
            return False
        expected = hmac.new(_AUTHORITY_SECRET, payload, sha256).digest()
        return hmac.compare_digest(observed, expected)


_AUTHORITY = _HmacAuthority()
_DEFAULT_REPLAY_GUARD = object()


class _SetReplayGuard:
    def __init__(self) -> None:
        self._consumed: set[tuple[str, str]] = set()

    def consume(
        self,
        *,
        backend_id: str,
        backend_run_id: str,
        authority_hash: str,
    ) -> bool:
        assert len(authority_hash) == 64
        identity = (backend_id, backend_run_id)
        if identity in self._consumed:
            return False
        self._consumed.add(identity)
        return True


class _CrashAfterFirstDurableConsume:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.crashed = False

    def consume(
        self,
        *,
        backend_id: str,
        backend_run_id: str,
        authority_hash: str,
    ) -> bool:
        consumed = self.delegate.consume(
            backend_id=backend_id,
            backend_run_id=backend_run_id,
            authority_hash=authority_hash,
        )
        if consumed and not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated crash after durable consumption")
        return consumed


class _AsyncRejectingAuthorityVerifier:
    async def verify(
        self,
        _payload: bytes,
        _signature: Mapping[str, str],
    ) -> bool:
        return False


class _TruthyRejectingAuthorityVerifier:
    def verify(
        self,
        _payload: bytes,
        _signature: Mapping[str, str],
    ) -> str:
        return "invalid-signature"


def _task_spec() -> TaskSpec:
    instance_id = "sympy__sympy-14711"
    revision = "1" * 40
    dataset_hash = "2" * 64
    split_hash = "3" * 64
    verifier_hash = "4" * 64
    image_digest = "sha256:" + ("5" * 64)
    problem_statement = "Preserve the public fixture behavior."
    row = {
        "instance_id": instance_id,
        "repo": "sympy/sympy",
        "base_commit": revision,
        "problem_statement": problem_statement,
        "dataset_name": "SWE-bench/SWE-bench_Verified",
        "dataset_hash": dataset_hash,
        "split": "test",
        "split_hash": split_hash,
    }
    resource_limits = {
        "timeout_s": 600,
        "max_workers": 1,
        "subprocess_timeout_s": 900,
        "memory_mb": 8192,
    }
    verifier_execution = {
        "dataset": {
            "name": "SWE-bench/SWE-bench_Verified",
            "hash": dataset_hash,
        },
        "split": {"name": "test", "hash": split_hash},
        "instance": {
            "id": instance_id,
            "row": row,
            "row_hash": _json_hash(row),
        },
        "repository": {
            "repo": "https://github.com/sympy/sympy.git",
            "canonical_repo_id": "sympy/sympy",
            "revision": revision,
            "base_commit": revision,
        },
        "verifier": {
            "id": "official-swebench",
            "version": "4.1.0",
            "package": "swebench==4.1.0",
            "hash": verifier_hash,
        },
        "container": {
            "image": (
                "swebench/sweb.eval.x86_64.sympy_14711"
                f"@{image_digest}"
            ),
            "digest": image_digest,
        },
        "platform": {
            "architecture": "x86_64",
            "os_name": "linux",
        },
        "network_policy": "disabled",
        "resource_limits": resource_limits,
        "harness": {
            "namespace": "swebench",
            "cache_level": "instance",
            "clean": False,
            "max_workers": 1,
            "timeout_s": 600,
            "subprocess_timeout_s": 900,
            "model_name": "supervisor-task-spec",
            "run_id_prefix": "supervisor-bound-oracle",
        },
    }
    return TaskSpec(
        task_id=instance_id,
        task_family="swebench-verified",
        repo="https://github.com/sympy/sympy.git",
        revision=revision,
        dataset_hash=dataset_hash,
        split_hash=split_hash,
        problem_statement=problem_statement,
        image_digest=image_digest,
        architecture="x86_64",
        os_name="linux",
        network_policy="disabled",
        resource_limits=resource_limits,
        verifier_id="official-swebench",
        verifier_hash=verifier_hash,
        canonical_task_key=instance_id,
        canonical_repo_id="sympy/sympy",
        metadata={"instance_id": instance_id},
        verifier_execution=verifier_execution,
    )


def _bound_frozen(task: TaskSpec) -> FrozenTaskResult:
    return bind_frozen_result_to_task(
        task,
        FrozenTaskResult.create(
            task_id=task.task_id,
            task_family=task.task_family,
            task_spec_hash=task.spec_hash,
            run_result_hash="6" * 64,
            patch="diff --git a/a.py b/a.py\n",
            output="done",
            metadata={},
            frozen_at_ms=1,
        ),
    )


def _verifier(
    task: TaskSpec,
    *,
    oracle_runner,
    authority_verifier=_AUTHORITY,
    trusted_backend_manifest_hashes=(
        _TRUSTED_BACKEND_MANIFEST_HASH,
    ),
    backend_run_replay_guard=_DEFAULT_REPLAY_GUARD,
) -> SweBenchVerifier:
    replay_guard = (
        _SetReplayGuard()
        if backend_run_replay_guard is _DEFAULT_REPLAY_GUARD
        else backend_run_replay_guard
    )
    return SweBenchVerifier(
        task_spec=task,
        verifier_version="4.1.0",
        verifier_hash=task.verifier_hash,
        oracle_runner=oracle_runner,
        authority_verifier=authority_verifier,
        trusted_backend_manifest_hashes=trusted_backend_manifest_hashes,
        backend_run_replay_guard=replay_guard,
    )


def _successful_oracle_result(
    context: dict,
    *,
    mode: str = "operational",
    backend_manifest_hash: str = _TRUSTED_BACKEND_MANIFEST_HASH,
    signer=_AUTHORITY,
    oracle_unavailable: bool = False,
    backend_run_id: str = "",
) -> dict:
    execution_spec = SweBenchVerifierExecutionSpec.from_mapping(
        context["verifier_execution_spec"]
    )
    expected_pins = execution_spec.execution_authority_pins()
    fail_to_pass_results = ["fixture::fixed"]
    pass_to_pass_results = ["fixture::existing"]
    outcome = {
        "return_code": 0,
        "oracle_unavailable": oracle_unavailable,
        "patch_applied": True,
        "report_sha256": sha256(b"fixture-report").hexdigest(),
        "report_instance_id": execution_spec.instance_id,
        "resolved": True,
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "fail_to_pass_results_hash": _json_hash(fail_to_pass_results),
        "pass_to_pass_results_hash": _json_hash(pass_to_pass_results),
    }
    execution_authority = build_swe_bench_execution_authority(
        execution_spec=execution_spec,
        mode=mode,
        backend_id="tests.attested-swebench-backend/v1",
        backend_manifest_hash=backend_manifest_hash,
        candidate_id=context["candidate_id"],
        model_patch_sha256=context["model_patch_sha256"],
        producer_run_result_hash=context["producer_run_result_hash"],
        request_nonce=context["request_nonce"],
        backend_run_id=(
            backend_run_id
            or "fixture-attested-run-" + context["request_nonce"][:16]
        ),
        observed_pins=expected_pins,
        pin_evidence={
            pin: {
                "kind": "fixture-backend-observation",
                "ref": f"fixture://execution/{pin}",
                "sha256": _json_hash({
                    "pin": pin,
                    "observed": expected_pins[pin],
                }),
            }
            for pin in SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS
        },
        outcome=outcome,
        signer=signer,
    )
    return {
        **outcome,
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_unavailable": oracle_unavailable,
        "verifier_execution_spec_hash": (
            context["verifier_execution_spec_hash"]
        ),
        "execution_authority": execution_authority,
        "execution_authority_hash": execution_authority["authority_hash"],
        "oracle_adapter_receipt": {
            **outcome,
            "schema_version": (
                SWE_BENCH_BOUND_ORACLE_RECEIPT_SCHEMA_VERSION
            ),
            "verifier_execution_spec": context["verifier_execution_spec"],
            "verifier_execution_spec_hash": (
                context["verifier_execution_spec_hash"]
            ),
            "candidate_id": context["candidate_id"],
            "model_patch_sha256": context["model_patch_sha256"],
            "producer_run_result_hash": (
                context["producer_run_result_hash"]
            ),
            "request_nonce": context["request_nonce"],
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_unavailable": oracle_unavailable,
            "execution_authority": execution_authority,
            "execution_authority_hash": (
                execution_authority["authority_hash"]
            ),
        },
    }


def _rehash_authority(authority: dict) -> None:
    body = {
        key: value
        for key, value in authority.items()
        if key not in {"authority_hash", "signature"}
    }
    authority["authority_hash"] = _json_hash(body)


def _replace_authority(result: dict, authority: dict) -> None:
    result["execution_authority"] = authority
    result["execution_authority_hash"] = authority["authority_hash"]
    receipt = result["oracle_adapter_receipt"]
    receipt["execution_authority"] = authority
    receipt["execution_authority_hash"] = authority["authority_hash"]


@pytest.mark.asyncio
async def test_swebench_verifier_binds_complete_task_spec_into_context_and_receipt():
    task = _task_spec()
    observed: dict = {}

    def oracle_runner(context):
        observed.update(copy.deepcopy(dict(context)))
        return _successful_oracle_result(dict(context))

    verifier = _verifier(task, oracle_runner=oracle_runner)
    frozen = _bound_frozen(task)

    grade = await verifier.verify(frozen)

    assert grade.passed is True
    assert observed["task_spec"] == task.to_dict()
    assert observed["task_spec_hash"] == task.spec_hash
    assert observed["dataset_name"] == "SWE-bench/SWE-bench_Verified"
    assert observed["dataset_hash"] == task.dataset_hash
    assert observed["split"] == "test"
    assert observed["split_hash"] == task.split_hash
    assert observed["instance_id"] == task.task_id
    assert observed["instance_row_hash"] == _json_hash(
        observed["instance_row"]
    )
    assert observed["repo"] == task.repo
    assert observed["revision"] == task.revision
    assert observed["base_commit"] == task.revision
    assert observed["verifier_package"] == "swebench==4.1.0"
    assert observed["verifier_hash"] == task.verifier_hash
    assert observed["container_image"].endswith(task.image_digest)
    assert observed["image_digest"] == task.image_digest
    assert observed["architecture"] == task.architecture
    assert observed["os_name"] == task.os_name
    assert observed["network_policy"] == task.network_policy
    assert observed["resource_limits"] == dict(task.resource_limits)
    assert observed["producer_run_result_hash"] == frozen.run_result_hash
    assert len(observed["request_nonce"]) == 64
    receipt = grade.evidence["oracle_adapter_receipt"]
    assert receipt["verifier_execution_spec"] == (
        observed["verifier_execution_spec"]
    )


@pytest.mark.asyncio
async def test_swebench_verifier_uses_fresh_nonce_and_backend_run_per_grade():
    task = _task_spec()
    observed: list[tuple[str, str]] = []

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        observed.append((
            context["request_nonce"],
            result["execution_authority"]["backend_run_id"],
        ))
        return result

    verifier = _verifier(task, oracle_runner=oracle_runner)
    frozen = _bound_frozen(task)

    await verifier.verify(frozen)
    await verifier.verify(frozen)

    assert observed[0][0] != observed[1][0]
    assert observed[0][1] != observed[1][1]


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_reused_backend_run_id():
    task = _task_spec()
    replay_guard = _SetReplayGuard()

    def oracle_runner(context):
        return _successful_oracle_result(
            dict(context),
            backend_run_id="fixture-reused-backend-run",
        )

    frozen = _bound_frozen(task)

    await _verifier(
        task,
        oracle_runner=oracle_runner,
        backend_run_replay_guard=replay_guard,
    ).verify(frozen)
    with pytest.raises(ValueError, match="already consumed"):
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            backend_run_replay_guard=replay_guard,
        ).verify(frozen)


@pytest.mark.asyncio
async def test_swebench_backend_run_replay_guard_survives_reconstruction(
    tmp_path: Path,
) -> None:
    task = _task_spec()
    database = tmp_path / "backend-run-replay.db"

    def oracle_runner(context):
        return _successful_oracle_result(
            dict(context),
            backend_run_id="fixture-durable-backend-run",
        )

    frozen = _bound_frozen(task)
    with SQLiteBackendRunReplayGuard(database) as first_guard:
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            backend_run_replay_guard=first_guard,
        ).verify(frozen)

    with SQLiteBackendRunReplayGuard(database) as reconstructed_guard:
        with pytest.raises(ValueError, match="different authority hash"):
            await _verifier(
                task,
                oracle_runner=oracle_runner,
                backend_run_replay_guard=reconstructed_guard,
            ).verify(frozen)


@pytest.mark.asyncio
async def test_swebench_retry_after_post_consume_crash_uses_fresh_backend_run(
    tmp_path: Path,
) -> None:
    task = _task_spec()
    database = tmp_path / "backend-run-replay.db"
    observed: list[tuple[str, str]] = []

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        observed.append((
            context["request_nonce"],
            result["execution_authority"]["backend_run_id"],
        ))
        return result

    frozen = _bound_frozen(task)
    with SQLiteBackendRunReplayGuard(database) as guard:
        crashing_guard = _CrashAfterFirstDurableConsume(guard)
        with pytest.raises(
            ValueError,
            match="simulated crash after durable consumption",
        ):
            await _verifier(
                task,
                oracle_runner=oracle_runner,
                backend_run_replay_guard=crashing_guard,
            ).verify(frozen)

        grade = await _verifier(
            task,
            oracle_runner=oracle_runner,
            backend_run_replay_guard=guard,
        ).verify(frozen)

        assert grade.passed is True
        assert observed[0][0] != observed[1][0]
        assert observed[0][1] != observed[1][1]
        assert guard._conn.execute(
            "SELECT COUNT(*) FROM swe_bench_backend_run_consumptions"
        ).fetchone()[0] == 2


def test_swebench_backend_run_replay_guard_detects_authority_discrepancy(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backend-run-replay.db"
    with SQLiteBackendRunReplayGuard(database) as first_guard:
        assert first_guard.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        )
    with SQLiteBackendRunReplayGuard(database) as guard:
        assert not guard.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        )
        with pytest.raises(
            BackendRunReplayConflictError,
            match="different authority hash",
        ):
            guard.consume(
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="b" * 64,
            )


def test_swebench_backend_run_replay_guard_rejects_forged_table_definition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backend-run-replay.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE swe_bench_backend_run_consumptions(
              schema_version TEXT,
              backend_id TEXT,
              backend_run_id TEXT,
              authority_hash TEXT,
              consumed_at_ms INTEGER
            )
            """
        )

    with pytest.raises(BackendRunReplayGuardError, match="schema"):
        SQLiteBackendRunReplayGuard(database)


def test_swebench_backend_run_replay_guard_rejects_forged_delete_trigger(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backend-run-replay.db"
    with SQLiteBackendRunReplayGuard(database) as guard:
        assert guard.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        )

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            DROP TRIGGER
              swe_bench_backend_run_consumptions_no_delete;
            CREATE TRIGGER
              swe_bench_backend_run_consumptions_no_delete
            BEFORE DELETE ON swe_bench_backend_run_consumptions
            BEGIN
              SELECT 1;
            END;
            DELETE FROM swe_bench_backend_run_consumptions
             WHERE backend_id = 'tests.backend/v1'
               AND backend_run_id = 'backend-run-1';
            """
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM swe_bench_backend_run_consumptions"
        ).fetchone()[0] == 0

    with pytest.raises(BackendRunReplayGuardError, match="schema"):
        SQLiteBackendRunReplayGuard(database)


def test_swebench_backend_run_replay_guard_requires_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute file path"):
        SQLiteBackendRunReplayGuard("relative/backend-run-replay.db")


@pytest.mark.parametrize("symlink_kind", ("file", "directory"))
def test_swebench_backend_run_replay_guard_rejects_symlink_components(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    if symlink_kind == "file":
        target = tmp_path / "target.db"
        sqlite3.connect(target).close()
        database = tmp_path / "backend-run-replay.db"
        database.symlink_to(target)
    else:
        target = tmp_path / "target"
        target.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        database = alias / "backend-run-replay.db"

    with pytest.raises(ValueError, match="symlink"):
        SQLiteBackendRunReplayGuard(database)


def test_swebench_backend_run_replay_guard_detects_live_path_replacement(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backend-run-replay.db"
    guard = SQLiteBackendRunReplayGuard(database)
    try:
        displaced = tmp_path / "displaced.db"
        database.replace(displaced)
        sqlite3.connect(database).close()

        with pytest.raises(RuntimeError, match="database identity changed"):
            guard.consume(
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
            )
    finally:
        guard.close()


def test_swebench_backend_run_replay_guard_fails_if_path_changes_mid_consume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_root = tmp_path / "replay-store"
    database = store_root / "backend-run-replay.db"
    guard = SQLiteBackendRunReplayGuard(database)
    original_database_identity = backend_run_replay_module._database_identity
    swapped = False

    def swap_after_preflight(path: Path, *, missing_ok: bool):
        nonlocal swapped
        identity = original_database_identity(path, missing_ok=missing_ok)
        if not swapped:
            swapped = True
            displaced = tmp_path / "displaced-replay-store"
            store_root.replace(displaced)
            store_root.mkdir()
            sqlite3.connect(database).close()
        return identity

    monkeypatch.setattr(
        backend_run_replay_module,
        "_database_identity",
        swap_after_preflight,
    )
    try:
        with pytest.raises(RuntimeError, match="database identity changed"):
            guard.consume(
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
            )
    finally:
        guard.close()


def test_swebench_backend_run_replay_database_is_durable_and_immutable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "backend-run-replay.db"
    with SQLiteBackendRunReplayGuard(database) as guard:
        assert (
            guard._conn.execute("PRAGMA journal_mode").fetchone()[0]
            == "wal"
        )
        assert guard._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert (
            guard._conn.execute("PRAGMA recursive_triggers").fetchone()[0]
            == 1
        )
        assert guard.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "PRAGMA recursive_triggers"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT OR REPLACE INTO
                swe_bench_backend_run_consumptions(
                  schema_version, backend_id, backend_run_id,
                  authority_hash, consumed_at_ms
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    BACKEND_RUN_REPLAY_SCHEMA_VERSION,
                    "tests.backend/v1",
                    "backend-run-1",
                    "b" * 64,
                    2,
                ),
            )


@pytest.mark.parametrize(
    "row",
    (
        ("wrong/v9", "backend", "run-1", "a" * 64, 1),
        (
            BACKEND_RUN_REPLAY_SCHEMA_VERSION,
            " ",
            "run-2",
            "a" * 64,
            1,
        ),
        (
            BACKEND_RUN_REPLAY_SCHEMA_VERSION,
            "backend",
            " ",
            "a" * 64,
            1,
        ),
        (
            BACKEND_RUN_REPLAY_SCHEMA_VERSION,
            "backend",
            "run-4",
            "A" * 64,
            1,
        ),
        (
            BACKEND_RUN_REPLAY_SCHEMA_VERSION,
            "backend",
            "run-5",
            "a" * 64,
            -1,
        ),
    ),
)
def test_swebench_backend_run_replay_database_rejects_invalid_rows(
    tmp_path: Path,
    row: tuple[object, ...],
) -> None:
    database = tmp_path / "backend-run-replay.db"
    with SQLiteBackendRunReplayGuard(database):
        pass
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO swe_bench_backend_run_consumptions(
                  schema_version, backend_id, backend_run_id,
                  authority_hash, consumed_at_ms
                ) VALUES(?, ?, ?, ?, ?)
                """,
                row,
            )


def test_swebench_verifier_execution_spec_is_detached_and_deeply_immutable():
    task = _task_spec()
    verifier = _verifier(task, oracle_runner=lambda _context: {})
    original_dataset = verifier.execution_spec.dataset_name

    task.verifier_execution["dataset"]["name"] = "attacker/substitute"
    serialized = verifier.execution_spec.to_dict()
    serialized["dataset"]["name"] = "caller/substitute"

    assert verifier.execution_spec.dataset_name == original_dataset
    with pytest.raises(TypeError):
        verifier.execution_spec.task_spec["repo"] = "substitute"
    with pytest.raises(TypeError):
        verifier.execution_spec.instance_row["repo"] = "other/project"
    with pytest.raises(TypeError):
        verifier.execution_spec.resource_limits["timeout_s"] = 1


def test_swebench_verifier_uses_task_spec_canonical_json_for_unicode():
    values = _task_spec().to_dict()
    statement = "Preserve café behavior."
    values["problem_statement"] = statement
    instance = values["verifier_execution"]["instance"]
    instance["row"]["problem_statement"] = statement
    instance["row_hash"] = _json_hash(instance["row"])
    task = TaskSpec.from_dict(values)

    verifier = _verifier(task, oracle_runner=lambda _context: {})

    assert verifier.execution_spec.task_spec_hash == task.spec_hash


@pytest.mark.parametrize(
    ("path", "replacement", "expected"),
    [
        (("dataset", "hash"), "a" * 64, "dataset hash"),
        (("split", "hash"), "a" * 64, "split hash"),
        (("repository", "repo"), "https://example.invalid/other.git", "repo"),
        (("repository", "revision"), "a" * 40, "revision"),
        (("verifier", "hash"), "a" * 64, "verifier package hash"),
        (("container", "digest"), "sha256:" + ("a" * 64), "container digest"),
        (("platform", "architecture"), "arm64", "architecture"),
        (("network_policy",), "enabled", "network policy"),
        (
            ("resource_limits", "memory_mb"),
            4096,
            "resource limits",
        ),
        (("harness", "timeout_s"), 601, "timeout_s"),
    ],
)
def test_swebench_verifier_rejects_execution_pin_mismatch_at_binding(
    path: tuple[str, ...],
    replacement: object,
    expected: str,
) -> None:
    values = _task_spec().to_dict()
    target = values["verifier_execution"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    task = TaskSpec.from_dict(values)

    with pytest.raises(ValueError, match=expected):
        _verifier(task, oracle_runner=lambda _context: {})


@pytest.mark.parametrize(
    ("row_key", "replacement", "expected"),
    [
        ("instance_id", "sympy__sympy-other", "row id"),
        ("repo", "other/project", "row repository"),
        ("base_commit", "a" * 40, "row base_commit"),
    ],
)
def test_swebench_verifier_rejects_instance_row_mismatch_at_binding(
    row_key: str,
    replacement: str,
    expected: str,
) -> None:
    values = _task_spec().to_dict()
    instance = values["verifier_execution"]["instance"]
    instance["row"][row_key] = replacement
    instance["row_hash"] = _json_hash(instance["row"])
    task = TaskSpec.from_dict(values)

    with pytest.raises(ValueError, match=expected):
        _verifier(task, oracle_runner=lambda _context: {})


def test_swebench_verifier_rejects_adapter_hash_mismatch() -> None:
    task = _task_spec()

    with pytest.raises(ValueError, match="package hash"):
        SweBenchVerifier(
            task_spec=task,
            verifier_version="4.1.0",
            verifier_hash="a" * 64,
            oracle_runner=lambda _context: {},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_key", "replacement"),
    [
        ("repo", "https://example.invalid/substitute.git"),
        ("revision", "a" * 40),
        ("instance_id", "sympy__sympy-other"),
        ("canonical_repo_id", "other/project"),
    ],
)
async def test_swebench_verifier_rejects_frozen_identity_mismatch_before_oracle(
    metadata_key: str,
    replacement: str,
) -> None:
    task = _task_spec()
    frozen = _bound_frozen(task)
    metadata = dict(frozen.metadata)
    metadata[metadata_key] = replacement
    substituted = FrozenTaskResult.create(
        task_id=frozen.task_id,
        task_family=frozen.task_family,
        task_spec_hash=frozen.task_spec_hash,
        run_result_hash=frozen.run_result_hash,
        patch=frozen.patch,
        output=frozen.output,
        metadata=metadata,
        frozen_at_ms=frozen.frozen_at_ms,
    )

    def must_not_run(_context):
        raise AssertionError("oracle must not run for mismatched authority")

    with pytest.raises(ValueError, match=metadata_key):
        await _verifier(task, oracle_runner=must_not_run).verify(substituted)


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_oracle_receipt_for_other_bound_spec():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        result["verifier_execution_spec_hash"] = "a" * 64
        return result

    with pytest.raises(ValueError, match="not bound"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_available_grade_without_execution_authority():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        result.pop("execution_authority")
        result.pop("execution_authority_hash")
        receipt = result["oracle_adapter_receipt"]
        receipt.pop("execution_authority")
        receipt.pop("execution_authority_hash")
        return result

    with pytest.raises(ValueError, match="execution authority"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pin",
    SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS,
)
async def test_swebench_verifier_rejects_mismatched_execution_authority_pin(
    pin: str,
) -> None:
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        authority = copy.deepcopy(result["execution_authority"])
        expected = authority["pins"][pin]["expected"]
        authority["pins"][pin]["observed"] = (
            {"substituted": True}
            if isinstance(expected, dict)
            else "substituted"
        )
        _rehash_authority(authority)
        _replace_authority(result, authority)
        return result

    with pytest.raises(ValueError, match=pin):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    (
        "candidate_id",
        "model_patch_sha256",
        "producer_run_result_hash",
        "request_nonce",
    ),
)
async def test_swebench_verifier_rejects_execution_authority_binding_mismatch(
    field: str,
) -> None:
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        authority = copy.deepcopy(result["execution_authority"])
        authority[field] = "a" * 64
        _rehash_authority(authority)
        _replace_authority(result, authority)
        return result

    with pytest.raises(ValueError, match=field):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_execution_authority_outcome_mismatch():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        authority = copy.deepcopy(result["execution_authority"])
        authority["outcome"]["resolved"] = False
        _rehash_authority(authority)
        _replace_authority(result, authority)
        result["resolved"] = False
        result["oracle_adapter_receipt"]["resolved"] = False
        return result

    with pytest.raises(ValueError, match="resolved status"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("container", "field", "substituted"),
    (
        ("result", "patch_applied", False),
        ("receipt", "return_code", 97),
    ),
)
async def test_swebench_verifier_rejects_unsigned_outcome_projection_mismatch(
    container: str,
    field: str,
    substituted: object,
) -> None:
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        target = (
            result
            if container == "result"
            else result["oracle_adapter_receipt"]
        )
        target[field] = substituted
        return result

    with pytest.raises(
        ValueError,
        match="execution authority outcome differs",
    ):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_unsigned_self_hashed_authority():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        authority = copy.deepcopy(result["execution_authority"])
        authority["signature"] = None
        _replace_authority(result, authority)
        return result

    with pytest.raises(ValueError, match="signature is invalid"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_fixture_authority_for_available_grade():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(
            dict(context),
            mode="fixture",
            signer=None,
        )

    with pytest.raises(ValueError, match="requires operational"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_untrusted_backend_manifest():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(
            dict(context),
            backend_manifest_hash="a" * 64,
        )

    with pytest.raises(ValueError, match="manifest is not trusted"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_operational_authority_without_trust_root():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(dict(context))

    with pytest.raises(ValueError, match="no configured trust verifier"):
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            authority_verifier=None,
        ).verify(_bound_frozen(task))


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_operational_authority_without_replay_guard():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(dict(context))

    with pytest.raises(ValueError, match="no backend run replay guard"):
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            backend_run_replay_guard=None,
        ).verify(_bound_frozen(task))


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_async_signature_verifier():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(dict(context))

    with pytest.raises(ValueError, match="must be synchronous"):
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            authority_verifier=_AsyncRejectingAuthorityVerifier(),
        ).verify(_bound_frozen(task))


@pytest.mark.asyncio
async def test_swebench_verifier_requires_exact_true_signature_verdict():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(dict(context))

    with pytest.raises(ValueError, match="signature is not trusted"):
        await _verifier(
            task,
            oracle_runner=oracle_runner,
            authority_verifier=_TruthyRejectingAuthorityVerifier(),
        ).verify(_bound_frozen(task))


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_signature_mismatch():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        authority = copy.deepcopy(result["execution_authority"])
        authority["signature"]["signature"] = base64.b64encode(
            b"not-the-authority-signature"
        ).decode("ascii")
        _replace_authority(result, authority)
        return result

    with pytest.raises(ValueError, match="signature is not trusted"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    (
        "candidate_id",
        "model_patch_sha256",
        "producer_run_result_hash",
        "request_nonce",
    ),
)
async def test_swebench_verifier_rejects_receipt_binding_mismatch(
    field: str,
) -> None:
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        result["oracle_adapter_receipt"][field] = "a" * 64
        return result

    with pytest.raises(ValueError, match=f"{field} binding mismatch"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_unknown_receipt_schema():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        result["oracle_adapter_receipt"]["schema_version"] = "unknown/v9"
        return result

    with pytest.raises(ValueError, match="schema version"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


@pytest.mark.asyncio
async def test_oracle_unavailable_flag_can_never_produce_a_passing_grade():
    task = _task_spec()

    def oracle_runner(context):
        return _successful_oracle_result(
            dict(context),
            oracle_unavailable=True,
        )

    grade = await _verifier(task, oracle_runner=oracle_runner).verify(
        _bound_frozen(task)
    )

    assert grade.passed is False
    assert grade.score == 0.0
    assert grade.failure_classification == (
        "verifier_infrastructure_unavailable"
    )


@pytest.mark.asyncio
async def test_swebench_verifier_allows_unavailable_result_without_authority():
    task = _task_spec()

    def oracle_runner(context):
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "backend_not_configured",
            "verifier_execution_spec_hash": (
                context["verifier_execution_spec_hash"]
            ),
            "oracle_adapter_receipt": {
                "schema_version": (
                    SWE_BENCH_BOUND_ORACLE_RECEIPT_SCHEMA_VERSION
                ),
                "verifier_execution_spec": (
                    context["verifier_execution_spec"]
                ),
                "verifier_execution_spec_hash": (
                    context["verifier_execution_spec_hash"]
                ),
                "candidate_id": context["candidate_id"],
                "model_patch_sha256": context["model_patch_sha256"],
                "producer_run_result_hash": (
                    context["producer_run_result_hash"]
                ),
                "request_nonce": context["request_nonce"],
                "fail_to_pass_status": "unavailable",
                "pass_to_pass_status": "unavailable",
                "oracle_unavailable": True,
            },
        }

    grade = await _verifier(task, oracle_runner=oracle_runner).verify(
        _bound_frozen(task)
    )

    assert grade.passed is False
    assert grade.score == 0.0
    assert grade.failure_classification == (
        "verifier_infrastructure_unavailable"
    )


def _oracle_context(task: TaskSpec) -> dict:
    verifier = _verifier(task, oracle_runner=lambda _context: {})
    frozen = _bound_frozen(task)
    return {
        **verifier.execution_spec.context_binding(),
        "candidate_id": frozen.result_hash,
        "frozen_result_hash": frozen.result_hash,
        "model_patch": frozen.patch,
        "model_patch_sha256": frozen.patch_hash,
        "producer_run_result_hash": frozen.run_result_hash,
        "request_nonce": "7" * 64,
    }


@pytest.mark.parametrize(
    ("context_key", "replacement"),
    [
        ("dataset_name", "substitute/dataset"),
        ("dataset", "substitute/dataset"),
        ("dataset_hash", "a" * 64),
        ("split", "validation"),
        ("split_hash", "a" * 64),
        ("instance_id", "sympy__sympy-other"),
        ("instance_row_hash", "a" * 64),
        ("repo", "https://example.invalid/other.git"),
        ("revision", "a" * 40),
        ("base_commit", "a" * 40),
        ("verifier_package", "swebench==0.0.0"),
        ("verifier_hash", "a" * 64),
        ("container_image", "example.invalid/other:latest"),
        ("image_digest", "sha256:" + ("a" * 64)),
        ("architecture", "arm64"),
        ("os_name", "macos"),
        ("network_policy", "enabled"),
        (
            "resource_limits",
            {
                "timeout_s": 1,
                "max_workers": 1,
                "subprocess_timeout_s": 2,
                "memory_mb": 1,
            },
        ),
    ],
)
def test_bound_oracle_rejects_context_mismatch_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context_key: str,
    replacement: object,
) -> None:
    context = _oracle_context(_task_spec())
    context["artifact_root"] = str(tmp_path / "oracle")
    context[context_key] = replacement

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("subprocess must not run for mismatched authority")

    monkeypatch.setattr(official_oracle.subprocess, "run", must_not_run)

    with pytest.raises(ValueError, match=context_key):
        run_task_spec_bound_official_harness_oracle(context)
    assert not (tmp_path / "oracle").exists()


def test_bound_oracle_fails_closed_without_execution_backend_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_spec()
    context = _oracle_context(task)
    context["artifact_root"] = str(tmp_path / "oracle")
    monkeypatch.setenv(
        "SWEBENCH_OFFICIAL_ORACLE_DATASET",
        "attacker/substitute",
    )
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "validation")

    def must_not_run(*_args, **_kwargs):
        raise AssertionError(
            "unattested stock CLI must not execute a gradeable run"
        )

    monkeypatch.setattr(official_oracle.subprocess, "run", must_not_run)

    result = run_task_spec_bound_official_harness_oracle(context)

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == (
        "execution_backend_attestation_required"
    )
    authority = result["execution_authority"]
    assert authority["enforced"] is False
    assert tuple(authority["unmet_pins"]) == (
        SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS
    )
    assert all(
        pin["enforced"] is False
        for pin in authority["pins"].values()
    )
    receipt = result["oracle_adapter_receipt"]
    assert receipt["task_spec_hash"] == task.spec_hash
    assert receipt["verifier_execution_spec"] == (
        context["verifier_execution_spec"]
    )
    assert receipt["execution_authority"] == authority
    assert receipt["execution_authority_hash"] == (
        authority["authority_hash"]
    )
    assert receipt["harness"]["run_id"] == authority["backend_run_id"]
    assert not (tmp_path / "oracle").exists()


@pytest.mark.asyncio
async def test_default_swebench_verifier_cannot_issue_unattested_passing_grade():
    task = _task_spec()

    grade = await SweBenchVerifier(
        task_spec=task,
        verifier_version="4.1.0",
        verifier_hash=task.verifier_hash,
    ).verify(_bound_frozen(task))

    assert grade.passed is False
    assert grade.score == 0.0
    assert grade.failure_classification == (
        "verifier_infrastructure_unavailable"
    )
    assert grade.evidence["oracle_unavailable_reason"] == (
        "execution_backend_attestation_required"
    )
    authority = grade.evidence["execution_authority"]
    assert authority["enforced"] is False
    assert tuple(authority["unmet_pins"]) == (
        SWE_BENCH_REQUIRED_EXECUTION_AUTHORITY_PINS
    )
