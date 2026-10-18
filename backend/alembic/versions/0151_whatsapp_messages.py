"""Add WhatsApp message IDs and routing.

Revision ID: 0151
Revises: 0150
"""

from alembic import op

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN external_id text")
    op.execute(
        "CREATE UNIQUE INDEX uq_messages_tenant_external_id ON messages (tenant_id, external_id) "
        "WHERE external_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE FUNCTION whatsapp_route(p_phone_number_id text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid, connection_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id, connection.id
            FROM channel_connections AS connection
            WHERE connection.kind = 'whatsapp'
              AND connection.external_id = p_phone_number_id
              AND connection.status = 'active'
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION whatsapp_route(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION whatsapp_route(text) TO inbound_app")
    op.execute("ALTER FUNCTION whatsapp_route(text) OWNER TO inbound_lookup")


def downgrade() -> None:
    op.execute("DROP FUNCTION whatsapp_route(text)")
    op.execute("DROP INDEX uq_messages_tenant_external_id")
    op.execute("ALTER TABLE messages DROP COLUMN external_id")
