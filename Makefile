-include .env
export

POSTGRES_USER ?= inbound_migrator
POSTGRES_PASSWORD ?= inbound_migrator
POSTGRES_DB ?= inbound
DATABASE_URL ?= postgresql+psycopg://inbound_app_login:inbound_dev_only@localhost:5432/inbound
MIGRATION_DATABASE_URL ?= postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:5432/$(POSTGRES_DB)
APP_DATABASE_URL ?= postgresql://inbound_app_login:inbound_dev_only@localhost:5432/inbound

.PHONY: up migrate test lint

up:
	docker compose up -d db

migrate:
	DATABASE_URL="$(DATABASE_URL)" MIGRATION_DATABASE_URL="$(MIGRATION_DATABASE_URL)" uv run --directory backend alembic upgrade head

test:
	uv run --directory core pytest
	DATABASE_URL="$(DATABASE_URL)" MIGRATION_DATABASE_URL="$(MIGRATION_DATABASE_URL)" APP_DATABASE_URL="$(APP_DATABASE_URL)" uv run --directory backend pytest
	uv run --directory voice pytest
	cd web && npm test

lint:
	uv run --directory core ruff check .
	uv run --directory backend ruff check .
	uv run --directory voice ruff check .
	cd web && npm run lint
