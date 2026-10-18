"""Platform API sessions and worker functions.

Revision ID: 0100
Revises: 0001
"""

from alembic import op

revision = "0100"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_sha256 text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            last_seen_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO inbound_app")
    op.execute(
        """
        CREATE FUNCTION claim_next_job(p_lease_seconds integer)
        RETURNS TABLE (id uuid, tenant_id uuid, kind text, payload jsonb, attempts integer)
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public AS $$
          UPDATE jobs SET status = 'running', attempts = jobs.attempts + 1,
                 run_after = now() + make_interval(secs => p_lease_seconds),
                 updated_at = now()
          WHERE jobs.id = (
            SELECT j.id FROM jobs AS j
            WHERE j.status IN ('queued', 'running') AND j.run_after <= now()
            ORDER BY j.run_after, j.created_at
            FOR UPDATE SKIP LOCKED LIMIT 1)
          RETURNING jobs.id, jobs.tenant_id, jobs.kind, jobs.payload, jobs.attempts
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION conversation_tenant(p_conversation_id uuid) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT tenant_id FROM conversations WHERE id = p_conversation_id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION claim_next_job(integer) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION conversation_tenant(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_job(integer) TO inbound_app")
    op.execute("GRANT EXECUTE ON FUNCTION conversation_tenant(uuid) TO inbound_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS conversation_tenant(uuid)")
    op.execute("DROP FUNCTION IF EXISTS claim_next_job(integer)")
    op.execute("DROP TABLE IF EXISTS user_sessions")
