"""Add after-conversation action runs and webhook secrets.

Revision ID: 0153
Revises: 0152
"""

from alembic import op

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE action_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            action_key text NOT NULL,
            action_kind text NOT NULL,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'succeeded', 'failed', 'skipped')),
            attempts integer NOT NULL DEFAULT 0,
            last_error text,
            output jsonb NOT NULL DEFAULT '{}'::jsonb,
            fired_events text[] NOT NULL DEFAULT '{}'::text[],
            pending_events text[] NOT NULL DEFAULT '{}'::text[],
            fire_count integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (conversation_id, action_key)
        )
        """
    )
    op.execute("CREATE INDEX ix_action_runs_tenant_id ON action_runs (tenant_id)")
    op.execute(
        "CREATE INDEX ix_action_runs_tenant_conversation "
        "ON action_runs (tenant_id, conversation_id)"
    )
    op.execute(
        """
        CREATE TABLE post_action_secrets (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            action_key text NOT NULL,
            secret_ciphertext text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (assistant_id, action_key)
        )
        """
    )
    op.execute("CREATE INDEX ix_post_action_secrets_tenant_id ON post_action_secrets (tenant_id)")
    for table in ("action_runs", "post_action_secrets"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table} FOR ALL
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO inbound_app")


def downgrade() -> None:
    op.execute("DROP TABLE post_action_secrets")
    op.execute("DROP TABLE action_runs")
