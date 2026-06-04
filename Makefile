.PHONY: migrate

POSTGRES_DSN ?=

migrate:
	@test -n "$(POSTGRES_DSN)" || (echo "POSTGRES_DSN is required, preferably a PgBouncer transaction-pool DSN for production" >&2; exit 2)
	DATABASE_URL="$(POSTGRES_DSN)" uv run --extra postgres alembic -c alembic.ini upgrade head
