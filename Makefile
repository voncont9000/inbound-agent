MIGRATION_DATABASE_URL ?= postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound
APP_DATABASE_URL ?= postgresql://inbound_app_login:inbound_dev_only@localhost:5432/inbound

.PHONY: up migrate test lint

up:
	docker compose up -d db

migrate:
	DATABASE_URL="$(MIGRATION_DATABASE_URL)" uv run --directory backend alembic upgrade head

test:
	uv run --directory core pytest
	MIGRATION_DATABASE_URL="$(MIGRATION_DATABASE_URL)" APP_DATABASE_URL="$(APP_DATABASE_URL)" uv run --directory backend pytest
	uv run --directory voice pytest
	cd web && npm test

lint:
	uv run --directory core ruff check .
	uv run --directory backend ruff check .
	uv run --directory voice ruff check .
	cd web && npm run lint
