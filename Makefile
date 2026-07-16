.PHONY: migrate test-postgres test-projection-registry

POSTGRES_DSN ?=

migrate:
	@test -n "$(POSTGRES_DSN)" || (echo "POSTGRES_DSN is required, preferably a PgBouncer transaction-pool DSN for production" >&2; exit 2)
	DATABASE_URL="$(POSTGRES_DSN)" uv run --extra postgres alembic -c alembic.ini upgrade head

test-postgres:
	./scripts/run_postgres_conformance.sh

test-projection-registry:
	env -u PYTHONPATH -u PYTEST_PLUGINS PYTHONNOUSERSITE=1 \
		uv run --extra dev python scripts/run_projection_registry_proofs.py
