import os
from uuid import uuid4

import psycopg

OWNER_DATABASE_URL = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql://inbound_migrator:inbound_migrator@localhost:5432/inbound",
)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    "postgresql://inbound_app_login:inbound_dev_only@localhost:5432/inbound",
)


def _execute(url: str, query: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def test_migration_has_applied() -> None:
    assert _execute(OWNER_DATABASE_URL, "SELECT version_num FROM alembic_version") == [("0001",)]


def test_tenant_rls_hides_and_blocks_updates_without_matching_tenant() -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    task_id = uuid4()
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name) VALUES (%s, %s), (%s, %s)",
            (tenant_a, "Tenant A", tenant_b, "Tenant B"),
        )
    try:
        with psycopg.connect(APP_DATABASE_URL) as connection:
            with connection.transaction():
                connection.execute(f"SET LOCAL app.tenant_id = '{tenant_a}'")
                connection.execute(
                    "INSERT INTO tasks (id, tenant_id, kind, source) VALUES (%s, %s, %s, %s)",
                    (task_id, tenant_a, "callback", "manual"),
                )
            with connection.transaction():
                connection.execute(f"SET LOCAL app.tenant_id = '{tenant_b}'")
                assert (
                    connection.execute("SELECT id FROM tasks WHERE id = %s", (task_id,)).fetchall()
                    == []
                )
                assert (
                    connection.execute(
                        "UPDATE tasks SET kind = 'changed' WHERE id = %s", (task_id,)
                    ).rowcount
                    == 0
                )
        with psycopg.connect(APP_DATABASE_URL) as connection:
            with connection.transaction():
                assert (
                    connection.execute("SELECT id FROM tasks WHERE id = %s", (task_id,)).fetchall()
                    == []
                )
                assert (
                    connection.execute(
                        "UPDATE tasks SET kind = 'changed' WHERE id = %s", (task_id,)
                    ).rowcount
                    == 0
                )
    finally:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            connection.execute("DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a, tenant_b))


def test_resolve_channel_returns_cross_tenant_route() -> None:
    tenant_id, assistant_id, connection_id = uuid4(), uuid4(), uuid4()
    external_number = f"+4420{uuid4().int % 10**8:08d}"
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "Routing"))
        connection.execute(
            "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, %s)",
            (assistant_id, tenant_id, "Routing assistant", "{}"),
        )
        connection.execute(
            "INSERT INTO channel_connections (id, tenant_id, assistant_id, kind, external_id, "
            "status) "
            "VALUES (%s, %s, %s, 'phone', %s, 'active')",
            (connection_id, tenant_id, assistant_id, external_number),
        )
    try:
        rows = _execute(
            APP_DATABASE_URL,
            "SELECT tenant_id, assistant_id FROM resolve_channel('phone', %s)",
            (external_number,),
        )
        assert rows == [(tenant_id, assistant_id)]
    finally:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
