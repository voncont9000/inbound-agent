"""Harden cross-tenant lookup and tenant references.

Revision ID: 0150
Revises: 0100
"""

import sqlalchemy as sa

from alembic import op

revision = "0150"
down_revision = "0100"
branch_labels = None
depends_on = None

_REFERENCED_TABLES = (
    "assistants",
    "knowledge_sources",
    "persons",
    "organisations",
    "conversations",
    "messages",
)

_TENANT_FOREIGN_KEYS = (
    ("assistant_config_versions", "assistant_id", "assistants", "CASCADE"),
    ("channel_connections", "assistant_id", "assistants", "CASCADE"),
    ("knowledge_sources", "assistant_id", "assistants", "CASCADE"),
    ("knowledge_chunks", "source_id", "knowledge_sources", "CASCADE"),
    ("knowledge_digests", "assistant_id", "assistants", "CASCADE"),
    ("knowledge_gaps", "assistant_id", "assistants", "CASCADE"),
    ("knowledge_gaps", "conversation_id", "conversations", "CASCADE"),
    ("persons", "merged_into_id", "persons", "NO ACTION"),
    ("person_org_roles", "person_id", "persons", "CASCADE"),
    ("person_org_roles", "organisation_id", "organisations", "CASCADE"),
    ("identities", "person_id", "persons", "CASCADE"),
    ("identity_merge_log", "survivor_id", "persons", "NO ACTION"),
    ("identity_merge_log", "merged_id", "persons", "NO ACTION"),
    ("facts", "source_conversation_id", "conversations", "SET NULL"),
    ("facts", "source_message_id", "messages", "SET NULL"),
    ("commitments", "person_id", "persons", "CASCADE"),
    ("commitments", "source_message_id", "messages", "CASCADE"),
    ("edges", "source_message_id", "messages", "SET NULL"),
    ("conversations", "assistant_id", "assistants", "NO ACTION"),
    ("conversations", "person_id", "persons", "SET NULL"),
    ("messages", "conversation_id", "conversations", "CASCADE"),
    ("intakes", "conversation_id", "conversations", "CASCADE"),
    ("intake_reviews", "conversation_id", "conversations", "CASCADE"),
    ("tasks", "conversation_id", "conversations", "SET NULL"),
    ("tasks", "person_id", "persons", "SET NULL"),
)


def _find_single_column_fk(table_name: str, column_name: str, referenced_table: str) -> str:
    constraint_names = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT constraint_row.conname
                FROM pg_constraint AS constraint_row
                WHERE constraint_row.contype = 'f'
                  AND constraint_row.conrelid = to_regclass(:table_name)
                  AND constraint_row.confrelid = to_regclass(:referenced_table)
                  AND constraint_row.conkey = ARRAY[
                      (
                          SELECT attribute.attnum
                          FROM pg_attribute AS attribute
                          WHERE attribute.attrelid = constraint_row.conrelid
                            AND attribute.attname = :column_name
                            AND NOT attribute.attisdropped
                      )
                  ]::smallint[]
                  AND constraint_row.confkey = ARRAY[
                      (
                          SELECT attribute.attnum
                          FROM pg_attribute AS attribute
                          WHERE attribute.attrelid = constraint_row.confrelid
                            AND attribute.attname = 'id'
                            AND NOT attribute.attisdropped
                      )
                  ]::smallint[]
                """
            ),
            {
                "table_name": f"public.{table_name}",
                "column_name": column_name,
                "referenced_table": f"public.{referenced_table}",
            },
        )
        .scalars()
        .all()
    )
    if len(constraint_names) != 1:
        raise RuntimeError(
            f"Expected one FK for {table_name}.{column_name} -> {referenced_table}.id, "
            f"found {constraint_names}"
        )
    return constraint_names[0]


def _constraint_name(table_name: str, column_name: str) -> str:
    return f"fk_{table_name}_{column_name}_tenant"


def _delete_action(action: str, column_name: str, *, composite: bool) -> str:
    if action == "SET NULL":
        if composite:
            return f" ON DELETE SET NULL ({column_name})"
        return " ON DELETE SET NULL"
    if action == "CASCADE":
        return " ON DELETE CASCADE"
    return ""


def _add_tenant_fk(
    table_name: str,
    column_name: str,
    referenced_table: str,
    delete_action: str,
    *,
    composite: bool,
) -> None:
    if composite:
        child_columns = f"tenant_id, {column_name}"
        parent_columns = "tenant_id, id"
    else:
        child_columns = column_name
        parent_columns = "id"
    name = _constraint_name(table_name, column_name)
    action = _delete_action(delete_action, column_name, composite=composite)
    op.execute(
        f"ALTER TABLE {table_name} ADD CONSTRAINT {name} "
        f"FOREIGN KEY ({child_columns}) REFERENCES {referenced_table} ({parent_columns})"
        f"{action}"
    )


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_lookup') THEN
                CREATE ROLE inbound_lookup NOLOGIN NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_job_claimer') THEN
                CREATE ROLE inbound_job_claimer NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "GRANT SELECT ON channel_connections, memberships, users, conversations TO inbound_lookup"
    )
    op.execute("GRANT SELECT, UPDATE ON jobs TO inbound_job_claimer")
    op.execute(
        "CREATE POLICY lookup_read ON channel_connections FOR SELECT TO inbound_lookup USING (true)"
    )
    op.execute("CREATE POLICY lookup_read ON memberships FOR SELECT TO inbound_lookup USING (true)")
    op.execute(
        "CREATE POLICY lookup_read ON conversations FOR SELECT TO inbound_lookup USING (true)"
    )
    op.execute(
        "CREATE POLICY job_claim ON jobs FOR ALL TO inbound_job_claimer "
        "USING (true) WITH CHECK (true)"
    )

    for table_name in _REFERENCED_TABLES:
        op.create_unique_constraint(f"uq_{table_name}_tenant_id", table_name, ["tenant_id", "id"])

    for table_name, column_name, referenced_table, delete_action in _TENANT_FOREIGN_KEYS:
        old_constraint_name = _find_single_column_fk(table_name, column_name, referenced_table)
        op.drop_constraint(old_constraint_name, table_name, type_="foreignkey")
        _add_tenant_fk(
            table_name,
            column_name,
            referenced_table,
            delete_action,
            composite=True,
        )

    op.execute("GRANT inbound_lookup TO CURRENT_USER")
    op.execute("GRANT inbound_job_claimer TO CURRENT_USER")
    op.execute("GRANT CREATE ON SCHEMA public TO inbound_lookup")
    op.execute("GRANT CREATE ON SCHEMA public TO inbound_job_claimer")
    op.execute("ALTER FUNCTION resolve_channel(channel_kind, text) OWNER TO inbound_lookup")
    op.execute("ALTER FUNCTION lookup_user_memberships(citext) OWNER TO inbound_lookup")
    op.execute("ALTER FUNCTION conversation_tenant(uuid) OWNER TO inbound_lookup")
    op.execute("ALTER FUNCTION claim_next_job(integer) OWNER TO inbound_job_claimer")
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_lookup")
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_job_claimer")


def downgrade() -> None:
    for table_name, column_name, referenced_table, delete_action in reversed(_TENANT_FOREIGN_KEYS):
        op.drop_constraint(
            _constraint_name(table_name, column_name), table_name, type_="foreignkey"
        )
        _add_tenant_fk(
            table_name,
            column_name,
            referenced_table,
            delete_action,
            composite=False,
        )

    for table_name in reversed(_REFERENCED_TABLES):
        op.drop_constraint(f"uq_{table_name}_tenant_id", table_name, type_="unique")

    op.execute("ALTER FUNCTION resolve_channel(channel_kind, text) OWNER TO CURRENT_USER")
    op.execute("ALTER FUNCTION lookup_user_memberships(citext) OWNER TO CURRENT_USER")
    op.execute("ALTER FUNCTION conversation_tenant(uuid) OWNER TO CURRENT_USER")
    op.execute("ALTER FUNCTION claim_next_job(integer) OWNER TO CURRENT_USER")
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_lookup")
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_job_claimer")
    op.execute("REVOKE inbound_lookup FROM CURRENT_USER")
    op.execute("REVOKE inbound_job_claimer FROM CURRENT_USER")
    op.execute("DROP POLICY lookup_read ON channel_connections")
    op.execute("DROP POLICY lookup_read ON memberships")
    op.execute("DROP POLICY lookup_read ON conversations")
    op.execute("DROP POLICY job_claim ON jobs")
    op.execute(
        "REVOKE SELECT ON channel_connections, memberships, users, "
        "conversations FROM inbound_lookup"
    )
    op.execute("REVOKE SELECT, UPDATE ON jobs FROM inbound_job_claimer")
    op.execute("DROP ROLE IF EXISTS inbound_job_claimer")
    op.execute("DROP ROLE IF EXISTS inbound_lookup")
