"""Persist calendar bookings and classify commitments.

Revision ID: 0160
Revises: 0152
"""

from alembic import op

revision = "0160"
down_revision = "0152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE commitments ADD COLUMN kind text")
    op.execute("UPDATE commitments SET kind = 'callback' WHERE what LIKE 'Call back:%'")
    op.execute(
        """
        CREATE TABLE calendar_bookings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            person_id uuid REFERENCES persons(id) ON DELETE SET NULL,
            calendar_id text NOT NULL,
            provider text NOT NULL,
            event_id text NOT NULL,
            event_link text,
            appointment_type text NOT NULL,
            slot_id text NOT NULL,
            start_at timestamptz NOT NULL,
            end_at timestamptz NOT NULL,
            attendee_name text NOT NULL,
            attendee_email text,
            attendee_phone text,
            status text NOT NULL DEFAULT 'booked'
                CHECK (status IN ('booked', 'cancelled')),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (conversation_id, slot_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_calendar_bookings_tenant_calendar_start "
        "ON calendar_bookings (tenant_id, calendar_id, start_at)"
    )
    op.execute("ALTER TABLE calendar_bookings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE calendar_bookings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON calendar_bookings FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("REVOKE ALL ON calendar_bookings FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON calendar_bookings TO inbound_app")


def downgrade() -> None:
    op.execute("DROP TABLE calendar_bookings")
    op.execute("ALTER TABLE commitments DROP COLUMN kind")
