"""Add review state to conversation quality scores.

Revision ID: 0181
Revises: 0180
"""

from alembic import op

revision = "0181"
down_revision = "0180"
branch_labels = None
depends_on = None


_FACTS_V1 = """
CREATE OR REPLACE FUNCTION admin_conversation_facts(p_since timestamptz)
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

_LIST_V1 = """
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

_FACTS_V2 = """
CREATE OR REPLACE FUNCTION admin_conversation_facts(p_since timestamptz)
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
        score.needs_review AND score.reviewed_at IS NULL,
        NULLIF(score.objective ->> 'response_p50_s', '')::double precision,
        NULLIF(score.objective ->> 'response_p95_s', '')::double precision
    FROM conversations AS conversation
    JOIN tenants AS tenant ON tenant.id = conversation.tenant_id
    LEFT JOIN conversation_scores AS score
        ON score.conversation_id = conversation.id
    WHERE conversation.started_at >= p_since
$$
"""

_LIST_V2 = """
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
    reviewed_at timestamptz,
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
        score.reviewed_at,
        COALESCE(score.issues, '[]'::jsonb),
        score.objective
    FROM conversations AS conversation
    JOIN tenants AS tenant ON tenant.id = conversation.tenant_id
    JOIN assistants AS assistant ON assistant.id = conversation.assistant_id
    LEFT JOIN conversation_scores AS score
        ON score.conversation_id = conversation.id
    WHERE (p_needs_review IS NULL OR
        (p_needs_review IS TRUE AND score.needs_review IS TRUE AND score.reviewed_at IS NULL) OR
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


def _set_admin_function_permissions() -> None:
    op.execute("GRANT CREATE ON SCHEMA public TO inbound_platform_reader")
    op.execute(
        "ALTER FUNCTION admin_conversation_facts(timestamptz) OWNER TO inbound_platform_reader"
    )
    op.execute(
        "ALTER FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) "
        "OWNER TO inbound_platform_reader"
    )
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
    op.execute("REVOKE CREATE ON SCHEMA public FROM inbound_platform_reader")


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_scores ADD COLUMN reviewed_at timestamptz")
    op.execute(
        "ALTER TABLE conversation_scores ADD COLUMN reviewed_by uuid "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER FUNCTION admin_conversation_facts(timestamptz) OWNER TO CURRENT_USER")
    op.execute(_FACTS_V2)
    op.execute(
        "ALTER FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) "
        "OWNER TO CURRENT_USER"
    )
    op.execute(
        "DROP FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer)"
    )
    op.execute(_LIST_V2)
    op.execute("GRANT inbound_platform_reader TO CURRENT_USER")
    _set_admin_function_permissions()


def downgrade() -> None:
    op.execute("ALTER FUNCTION admin_conversation_facts(timestamptz) OWNER TO CURRENT_USER")
    op.execute(
        "ALTER FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer) "
        "OWNER TO CURRENT_USER"
    )
    op.execute(
        "DROP FUNCTION admin_conversation_list(boolean, uuid, uuid, timestamptz, uuid, integer)"
    )
    op.execute(_FACTS_V1)
    op.execute(_LIST_V1)
    _set_admin_function_permissions()
    op.execute("ALTER TABLE conversation_scores DROP COLUMN reviewed_by")
    op.execute("ALTER TABLE conversation_scores DROP COLUMN reviewed_at")
