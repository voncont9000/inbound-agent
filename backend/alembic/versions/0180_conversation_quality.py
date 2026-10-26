"""Add conversation quality scores and evaluation cases.

Revision ID: 0180
Revises: 0173
"""

from alembic import op

revision = "0180"
down_revision = "0173"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_platform_reader') THEN
                CREATE ROLE inbound_platform_reader NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE conversation_scores (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL UNIQUE
                REFERENCES conversations(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            judge_model text NOT NULL,
            judge_version text NOT NULL,
            overall integer NOT NULL CHECK (overall BETWEEN 1 AND 5),
            needs_review boolean NOT NULL,
            checks jsonb NOT NULL DEFAULT '[]'::jsonb,
            issues jsonb NOT NULL DEFAULT '[]'::jsonb,
            objective jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversation_scores_tenant_needs_review "
        "ON conversation_scores (tenant_id, needs_review)"
    )
    op.execute("ALTER TABLE conversation_scores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversation_scores FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON conversation_scores FOR ALL
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_scores TO inbound_app")
    op.execute(
        """
        CREATE TABLE eval_cases (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
            name text NOT NULL,
            assistant_id uuid REFERENCES assistants(id) ON DELETE SET NULL,
            caller_utterances jsonb NOT NULL DEFAULT '[]'::jsonb,
            checks jsonb NOT NULL DEFAULT
                '{"transcript_checks":{},"assistant_checks":{},"intake_checks":{}}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            enabled boolean NOT NULL DEFAULT true
        )
        """
    )
    op.execute(
        """
        CREATE TABLE eval_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            git_sha text,
            trigger text NOT NULL DEFAULT 'manual',
            summary jsonb NOT NULL DEFAULT
                jsonb_build_object('cases', 0, 'passed', 0, 'failed', 0),
            results jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON eval_cases, eval_runs TO inbound_app")
    op.execute(
        "GRANT SELECT ON tenants, assistants, conversations, conversation_scores "
        "TO inbound_platform_reader"
    )
    op.execute(
        "CREATE POLICY platform_read ON assistants FOR SELECT "
        "TO inbound_platform_reader USING (true)"
    )
    op.execute(
        "CREATE POLICY platform_read ON conversations FOR SELECT "
        "TO inbound_platform_reader USING (true)"
    )
    op.execute(
        "CREATE POLICY platform_read ON conversation_scores FOR SELECT "
        "TO inbound_platform_reader USING (true)"
    )
    op.execute(
        """
        CREATE FUNCTION admin_conversation_facts(p_since timestamptz)
        RETURNS TABLE (
            conversation_id uuid,
            tenant_id uuid,
            tenant_name text,
            started_at timestamptz,
            scored boolean,
            overall integer,
            needs_review boolean,
            response_p50_s double precision,
            response_p95_s double precision
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT
                conversation.id,
                conversation.tenant_id,
                tenant.name,
                conversation.started_at,
                score.id IS NOT NULL,
                score.overall,
                score.needs_review,
                NULLIF(score.objective ->> 'response_p50_s', '')::double precision,
                NULLIF(score.objective ->> 'response_p95_s', '')::double precision
            FROM conversations AS conversation
            JOIN tenants AS tenant ON tenant.id = conversation.tenant_id
            LEFT JOIN conversation_scores AS score
                ON score.conversation_id = conversation.id
            WHERE conversation.started_at >= p_since
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION admin_conversation_list(
            p_needs_review boolean,
            p_tenant_id uuid,
            p_assistant_id uuid,
            p_cursor_started timestamptz,
            p_cursor_id uuid,
            p_limit integer
        )
        RETURNS TABLE (
            conversation_id uuid,
            tenant_id uuid,
            tenant_name text,
            assistant_id uuid,
            assistant_name text,
            channel text,
            started_at timestamptz,
            is_test boolean,
            overall integer,
            needs_review boolean,
            issues jsonb,
            objective jsonb
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT
                conversation.id,
                conversation.tenant_id,
                tenant.name,
                assistant.id,
                assistant.name,
                conversation.channel::text,
                conversation.started_at,
                COALESCE((conversation.metrics ->> 'test_call')::boolean, false),
                score.overall,
                score.needs_review,
                COALESCE(score.issues, '[]'::jsonb),
                score.objective
            FROM conversations AS conversation
            JOIN tenants AS tenant ON tenant.id = conversation.tenant_id
            JOIN assistants AS assistant ON assistant.id = conversation.assistant_id
            LEFT JOIN conversation_scores AS score
                ON score.conversation_id = conversation.id
            WHERE (p_needs_review IS NULL OR
                (p_needs_review IS TRUE AND score.needs_review IS TRUE) OR
                (p_needs_review IS FALSE AND score.id IS NOT NULL AND score.needs_review IS FALSE))
              AND (p_tenant_id IS NULL OR conversation.tenant_id = p_tenant_id)
              AND (p_assistant_id IS NULL OR conversation.assistant_id = p_assistant_id)
              AND (
                p_cursor_started IS NULL
                OR (conversation.started_at, conversation.id) < (p_cursor_started, p_cursor_id)
              )
            ORDER BY conversation.started_at DESC, conversation.id DESC
            LIMIT p_limit
        $$
        """
    )
    op.execute("GRANT inbound_platform_reader TO CURRENT_USER")
    op.execute("GRANT CREATE ON SCHEMA public TO inbound_platform_reader")
    op.execute(
        "ALTER FUNCTION admin_conversation_facts(timestamptz) OWNER TO inbound_platform_reader"
    )
    op.execute(
        "ALTER FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) "
        "OWNER TO inbound_platform_reader"
    )
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_platform_reader")
    op.execute("REVOKE ALL ON FUNCTION admin_conversation_facts(timestamptz) FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) FROM PUBLIC"
    )
    op.execute("GRANT EXECUTE ON FUNCTION admin_conversation_facts(timestamptz) TO inbound_app")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) TO inbound_app"
    )


def downgrade() -> None:
    op.execute("ALTER FUNCTION admin_conversation_facts(timestamptz) OWNER TO CURRENT_USER")
    op.execute(
        "ALTER FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) "
        "OWNER TO CURRENT_USER"
    )
    op.execute(
        "DROP FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer)"
    )
    op.execute("DROP FUNCTION admin_conversation_facts(timestamptz)")
    op.execute("DROP TABLE eval_runs")
    op.execute("DROP TABLE eval_cases")
    op.execute("DROP POLICY platform_read ON conversation_scores")
    op.execute("DROP POLICY platform_read ON conversations")
    op.execute("DROP POLICY platform_read ON assistants")
    op.execute(
        "REVOKE SELECT ON tenants, assistants, conversations, "
        "conversation_scores FROM inbound_platform_reader"
    )
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_platform_reader")
    op.execute("REVOKE inbound_platform_reader FROM CURRENT_USER")
    op.execute("DROP TABLE conversation_scores")
    op.execute("DROP ROLE IF EXISTS inbound_platform_reader")
