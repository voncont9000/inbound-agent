"""Store tenant-scoped call recordings with retention metadata.

Revision ID: 0170
Revises: 0172
"""

from alembic import op

revision = "0170"
down_revision = "0172"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE call_recordings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE UNIQUE,
            storage_key text NOT NULL,
            content_type text NOT NULL,
            size_bytes bigint NOT NULL,
            duration_seconds double precision,
            is_test boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_call_recordings_tenant_expires ON call_recordings (tenant_id, expires_at)"
    )
    op.execute("ALTER TABLE call_recordings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE call_recordings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON call_recordings FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("REVOKE ALL ON call_recordings FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON call_recordings TO inbound_app")


def downgrade() -> None:
    op.execute("DROP TABLE call_recordings")
