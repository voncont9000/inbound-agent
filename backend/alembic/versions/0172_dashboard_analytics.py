"""Index dashboard joins and backfill text-channel conversation costs.

Revision ID: 0172
Revises: 0160
"""

from alembic import op

revision = "0172"
down_revision = "0160"
branch_labels = None
depends_on = None

BACKFILL_TEXT_COSTS_SQL = """
        WITH costs AS (
            SELECT m.conversation_id, m.meta->'cost' AS cost
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE c.estimated_cost IS NULL
              AND c.channel IN ('whatsapp', 'gmail')
              AND m.role = 'assistant'
              AND jsonb_typeof(m.meta->'cost') = 'object'
              AND jsonb_typeof(m.meta->'cost'->'total') = 'number'
        ),
        totals AS (
            SELECT conversation_id,
                   sum((cost->>'total')::numeric) AS total,
                   max(cost->>'currency') AS currency
            FROM costs
            GROUP BY conversation_id
        ),
        components AS (
            SELECT conversation_id, jsonb_object_agg(key, amount) AS components
            FROM (
                SELECT costs.conversation_id, item.key,
                       sum((item.value #>> '{}')::numeric) AS amount
                FROM costs
                CROSS JOIN LATERAL jsonb_each(
                    CASE WHEN jsonb_typeof(costs.cost->'components') = 'object'
                         THEN costs.cost->'components' ELSE '{}'::jsonb END
                ) AS item
                WHERE jsonb_typeof(item.value) = 'number'
                GROUP BY costs.conversation_id, item.key
            ) AS summed
            GROUP BY conversation_id
        )
        UPDATE conversations
        SET estimated_cost = jsonb_build_object(
            'currency', coalesce(totals.currency, 'USD'),
            'components', coalesce(components.components, '{}'::jsonb),
            'total', totals.total,
            'unknown_components', '[]'::jsonb
        )
        FROM totals
        LEFT JOIN components ON components.conversation_id = totals.conversation_id
        WHERE conversations.id = totals.conversation_id
        """


def upgrade() -> None:
    op.execute("CREATE INDEX ix_tasks_tenant_conversation ON tasks (tenant_id, conversation_id)")
    op.execute(
        "CREATE INDEX ix_calendar_bookings_tenant_conversation "
        "ON calendar_bookings (tenant_id, conversation_id)"
    )
    op.execute(BACKFILL_TEXT_COSTS_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calendar_bookings_tenant_conversation")
    op.execute("DROP INDEX IF EXISTS ix_tasks_tenant_conversation")
