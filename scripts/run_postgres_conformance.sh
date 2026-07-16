#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 0 ]]; then
  echo "PostgreSQL conformance does not accept pytest selection arguments" >&2
  exit 2
fi

run_postgres_tests() {
  local report collected expected_count
  local -a pytest_plugins
  pytest_plugins=(-p pytest_asyncio.plugin)
  if [[ -n "${CODEX_PROJECTION_PROOF_RECEIPT:-}" ]]; then
    pytest_plugins+=(-p scripts.projection_proof_plugin)
  fi
  report="$(mktemp "${TMPDIR:-/tmp}/codex-postgres-junit.XXXXXX")"
  collected="$(mktemp "${TMPDIR:-/tmp}/codex-postgres-collected.XXXXXX")"
  cleanup_reports() {
    rm -f "${report}" "${collected}"
  }
  if ! env -u PYTEST_PLUGINS -u PYTHONPATH \
    PYTEST_ADDOPTS="" \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONNOUSERSITE=1 \
    uv run --extra dev --extra postgres python -m pytest \
    -p pytest_asyncio.plugin \
    -o addopts= \
    --collect-only -q tests/test_postgres_ledger_lane.py \
    | awk '/^tests\/test_postgres_ledger_lane.py::/' \
    >"${collected}"; then
    cleanup_reports
    return 1
  fi
  if ! diff -u tests/postgres_conformance_manifest.txt "${collected}"; then
    echo "PostgreSQL conformance manifest differs from collected tests" >&2
    cleanup_reports
    return 1
  fi
  expected_count="$(
    awk 'NF { count += 1 } END { print count + 0 }' \
      tests/postgres_conformance_manifest.txt
  )"
  if ! env -u PYTEST_PLUGINS -u PYTHONPATH \
    PYTEST_ADDOPTS="" \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONNOUSERSITE=1 \
    uv run --extra dev --extra postgres python -m pytest \
    "${pytest_plugins[@]}" -o addopts= -q \
    tests/test_postgres_ledger_lane.py \
    --junitxml="${report}"; then
    cleanup_reports
    return 1
  fi
  if ! uv run python - "${report}" "${expected_count}" <<'PY'
import sys
import xml.etree.ElementTree as ET

report_path, raw_expected = sys.argv[1:]
expected = int(raw_expected)
root = ET.parse(report_path).getroot()
cases = root.findall(".//testcase")
skipped = [case for case in cases if case.find("skipped") is not None]
if len(cases) != expected:
    raise SystemExit(
        "PostgreSQL conformance executed "
        f"{len(cases)} tests; expected complete manifest of {expected}"
    )
if skipped:
    raise SystemExit(
        "PostgreSQL conformance is invalid because "
        f"{len(skipped)} tests skipped"
    )
PY
  then
    cleanup_reports
    return 1
  fi
  cleanup_reports
}

if [[ -n "${CODEX_SUPERVISOR_POSTGRES_TEST_DSN:-}" ]]; then
  run_postgres_tests
  exit $?
fi

command -v docker >/dev/null 2>&1 || {
  echo "docker is required when CODEX_SUPERVISOR_POSTGRES_TEST_DSN is unset" >&2
  exit 2
}
command -v uv >/dev/null 2>&1 || {
  echo "uv is required to run the PostgreSQL conformance suite" >&2
  exit 2
}

image="${CODEX_SUPERVISOR_POSTGRES_TEST_IMAGE:-postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777}"
container_name="codex-supervisor-postgres-test-$$"
database="codex_supervisor"
username="postgres"
password="codex-supervisor-test"

cleanup() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run -d --name "${container_name}" \
  -e "POSTGRES_DB=${database}" \
  -e "POSTGRES_PASSWORD=${password}" \
  -p 127.0.0.1::5432 \
  "${image}" >/dev/null

port="$(docker port "${container_name}" 5432/tcp | awk -F: 'END { print $NF }')"
if [[ -z "${port}" ]]; then
  echo "failed to resolve the temporary PostgreSQL port" >&2
  exit 1
fi

for attempt in $(seq 1 60); do
  if docker exec "${container_name}" \
    pg_isready -U "${username}" -d "${database}" >/dev/null 2>&1; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    docker logs "${container_name}" >&2
    echo "temporary PostgreSQL did not become ready" >&2
    exit 1
  fi
  sleep 1
done

export CODEX_SUPERVISOR_POSTGRES_TEST_DSN="postgresql://${username}:${password}@127.0.0.1:${port}/${database}"
echo "Running PostgreSQL conformance against ${image} on an ephemeral local port"
run_postgres_tests
