"""Add encrypted Gmail credentials and mailbox routing.

Revision ID: 0152
Revises: 0151
"""

from alembic import op

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE channel_connections ADD COLUMN credentials_ciphertext text")
    op.execute(
        """
        CREATE FUNCTION gmail_route(p_email text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid, connection_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id, connection.id
            FROM channel_connections AS connection
            WHERE connection.kind = 'gmail'
              AND connection.external_id = lower(p_email)
              AND connection.status = 'active'
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION gmail_route(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION gmail_route(text) TO inbound_app")
    op.execute("ALTER FUNCTION gmail_route(text) OWNER TO inbound_lookup")


def downgrade() -> None:
    op.execute("DROP FUNCTION gmail_route(text)")
    op.execute("ALTER TABLE channel_connections DROP COLUMN credentials_ciphertext")
