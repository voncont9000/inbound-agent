"""Separate Google feature credentials from Gmail inbox connections."""

from alembic import op

revision = "0182"
down_revision = "0181"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE channel_kind ADD VALUE IF NOT EXISTS 'google'")
    op.drop_constraint(
        "channel_connections_kind_external_id_key", "channel_connections", type_="unique"
    )
    op.create_index(
        "channel_connections_kind_external_id_non_google_key",
        "channel_connections",
        ["kind", "external_id"],
        unique=True,
        postgresql_where="kind <> 'google'",
    )
    op.create_index(
        "channel_connections_tenant_external_id_google_key",
        "channel_connections",
        ["tenant_id", "external_id"],
        unique=True,
        postgresql_where="kind = 'google'",
    )


def downgrade() -> None:
    op.drop_index(
        "channel_connections_tenant_external_id_google_key",
        table_name="channel_connections",
    )
    op.drop_index(
        "channel_connections_kind_external_id_non_google_key",
        table_name="channel_connections",
    )
    op.create_unique_constraint(
        "channel_connections_kind_external_id_key",
        "channel_connections",
        ["kind", "external_id"],
    )
