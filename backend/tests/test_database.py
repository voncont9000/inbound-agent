import os
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg.errors import ForeignKeyViolation

from inbound_api.db import engine


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


OWNER_DATABASE_URL = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound",
)
OWNER_DATABASE_URL = _psycopg_url(OWNER_DATABASE_URL)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    "postgresql://inbound_app_login:inbound_dev_only@localhost:5432/inbound",
)


def _execute(url: str, query: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _set_tenant(connection, tenant_id) -> None:
    connection.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def test_application_engine_uses_non_superuser_non_bypass_role() -> None:
    engine_url = _psycopg_url(engine.url.render_as_string(hide_password=False))
    assert _execute(
        engine_url,
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    ) == [(False, False)]


def test_migration_has_applied() -> None:
    applied = {
        version
        for (version,) in _execute(OWNER_DATABASE_URL, "SELECT version_num FROM alembic_version")
    }
    expected = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    assert applied == expected


def test_security_definer_functions_have_non_bypass_owners() -> None:
    functions = _execute(
        OWNER_DATABASE_URL,
        """
        SELECT procedure.proname, owner.rolname, owner.rolsuper, owner.rolbypassrls
        FROM pg_proc AS procedure
        JOIN pg_roles AS owner ON owner.oid = procedure.proowner
        WHERE procedure.pronamespace = 'public'::regnamespace
          AND procedure.prosecdef
        ORDER BY procedure.proname
        """,
    )
    assert functions == [
        ("claim_next_job", "inbound_job_claimer", False, False),
        ("conversation_tenant", "inbound_lookup", False, False),
        ("lookup_user_memberships", "inbound_lookup", False, False),
        ("resolve_channel", "inbound_lookup", False, False),
        ("whatsapp_route", "inbound_lookup", False, False),
    ]


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
            for tenant_id in (tenant_a, tenant_b):
                _set_tenant(connection, tenant_id)
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_resolve_channel_returns_cross_tenant_route() -> None:
    tenant_id, assistant_id, connection_id = uuid4(), uuid4(), uuid4()
    external_number = f"+4420{uuid4().int % 10**8:08d}"
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "Routing"))
        _set_tenant(connection, tenant_id)
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
            _set_tenant(connection, tenant_id)
            connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_composite_message_fk_rejects_cross_tenant_conversation() -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    assistant_a, assistant_b = uuid4(), uuid4()
    conversation_a, conversation_b = uuid4(), uuid4()
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO tenants (id, name) VALUES (%s, %s), (%s, %s)",
            (tenant_a, "Message A", tenant_b, "Message B"),
        )
        for tenant_id, assistant_id, conversation_id, external_ref in (
            (tenant_a, assistant_a, conversation_a, "conversation-a"),
            (tenant_b, assistant_b, conversation_b, "conversation-b"),
        ):
            _set_tenant(connection, tenant_id)
            connection.execute(
                "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, '{}')",
                (
                    assistant_id,
                    tenant_id,
                    f"Assistant {external_ref}",
                ),
            )
            connection.execute(
                "INSERT INTO conversations "
                "(id, tenant_id, assistant_id, channel, external_ref, status, started_at) "
                "VALUES (%s, %s, %s, 'web', %s, 'active', now())",
                (conversation_id, tenant_id, assistant_id, external_ref),
            )
    try:
        with psycopg.connect(APP_DATABASE_URL) as connection:
            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (str(tenant_b),)
                    )
                    connection.execute(
                        "INSERT INTO messages "
                        "(tenant_id, conversation_id, role, text, ts) "
                        "VALUES (%s, %s, 'caller', 'cross-tenant', now())",
                        (tenant_b, conversation_a),
                    )
            with connection.transaction():
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_b),))
                connection.execute(
                    "INSERT INTO messages "
                    "(tenant_id, conversation_id, role, text, ts) "
                    "VALUES (%s, %s, 'caller', 'same-tenant', now())",
                    (tenant_b, conversation_b),
                )
    finally:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            for tenant_id in (tenant_a, tenant_b):
                _set_tenant(connection, tenant_id)
                connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_deleting_person_nulls_only_conversation_person_id() -> None:
    tenant_id, assistant_id, person_id, conversation_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "Person"))
        _set_tenant(connection, tenant_id)
        connection.execute(
            "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, '{}')",
            (assistant_id, tenant_id, "Person assistant"),
        )
        connection.execute(
            "INSERT INTO persons (id, tenant_id, display_name) VALUES (%s, %s, %s)",
            (person_id, tenant_id, "To delete"),
        )
        connection.execute(
            "INSERT INTO conversations "
            "(id, tenant_id, assistant_id, person_id, channel, external_ref, status, started_at) "
            "VALUES (%s, %s, %s, %s, 'web', 'person-conversation', 'active', now())",
            (conversation_id, tenant_id, assistant_id, person_id),
        )
    try:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            _set_tenant(connection, tenant_id)
            connection.execute("DELETE FROM persons WHERE id = %s", (person_id,))
            assert connection.execute(
                "SELECT tenant_id, person_id FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchall() == [(tenant_id, None)]
    finally:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            _set_tenant(connection, tenant_id)
            connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
