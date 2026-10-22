"""Allow channel connections to remain after assistant deletion.

Revision ID: 0173
Revises: 0170
"""

from alembic import op

revision = "0173"
down_revision = "0170"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE channel_connections ALTER COLUMN assistant_id DROP NOT NULL")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_channel(p_kind channel_kind, p_external_id text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id
            FROM channel_connections AS connection
            WHERE connection.kind = p_kind
              AND connection.external_id = p_external_id
              AND connection.assistant_id IS NOT NULL
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION whatsapp_route(p_phone_number_id text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid, connection_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id, connection.id
            FROM channel_connections AS connection
            WHERE connection.kind = 'whatsapp'
              AND connection.external_id = p_phone_number_id
              AND connection.status = 'active'
              AND connection.assistant_id IS NOT NULL
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION gmail_route(p_email text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid, connection_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id, connection.id
            FROM channel_connections AS connection
            WHERE connection.kind = 'gmail'
              AND connection.external_id = lower(p_email)
              AND connection.status = 'active'
              AND connection.assistant_id IS NOT NULL
        $$
        """
    )


def downgrade() -> None:
    # The original schema cannot represent unassigned channels.
    op.execute("DELETE FROM channel_connections WHERE assistant_id IS NULL")
    op.execute("ALTER TABLE channel_connections ALTER COLUMN assistant_id SET NOT NULL")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_channel(p_kind channel_kind, p_external_id text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id
            FROM channel_connections AS connection
            WHERE connection.kind = p_kind AND connection.external_id = p_external_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION whatsapp_route(p_phone_number_id text)
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION gmail_route(p_email text)
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
