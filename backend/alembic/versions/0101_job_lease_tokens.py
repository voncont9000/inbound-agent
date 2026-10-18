"""Add lease tokens to durable jobs.

Revision ID: 0101
Revises: 0100
"""

from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN lease_token uuid")
    op.execute("DROP FUNCTION claim_next_job(integer)")
    op.execute(
        """
        CREATE FUNCTION claim_next_job(p_lease_seconds integer)
        RETURNS TABLE (
            id uuid,
            tenant_id uuid,
            kind text,
            payload jsonb,
            attempts integer,
            lease_token uuid
        )
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public AS $$
          UPDATE jobs SET status = 'running',
                 attempts = jobs.attempts + 1,
                 lease_token = gen_random_uuid(),
                 run_after = now() + make_interval(secs => p_lease_seconds),
                 updated_at = now()
          WHERE jobs.id = (
            SELECT j.id FROM jobs AS j
            WHERE j.status IN ('queued', 'running') AND j.run_after <= now()
            ORDER BY j.run_after, j.created_at
            FOR UPDATE SKIP LOCKED LIMIT 1)
          RETURNING jobs.id, jobs.tenant_id, jobs.kind, jobs.payload,
                    jobs.attempts, jobs.lease_token
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION claim_next_job(integer) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_job(integer) TO inbound_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS claim_next_job(integer)")
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
    op.execute("REVOKE ALL ON FUNCTION claim_next_job(integer) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_job(integer) TO inbound_app")
    op.execute("ALTER TABLE jobs DROP COLUMN lease_token")
