"""M1 shared foundation.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "audit_log",
    "jobs",
    "tasks",
    "intake_reviews",
    "intakes",
    "messages",
    "conversations",
    "embeddings",
    "edges",
    "commitments",
    "facts",
    "identity_merge_log",
    "identities",
    "person_org_roles",
    "organisations",
    "persons",
    "knowledge_gaps",
    "knowledge_digests",
    "knowledge_chunks",
    "knowledge_sources",
    "channel_connections",
    "assistant_config_versions",
    "assistants",
    "memberships",
    "users",
    "tenants",
)

_TENANT_TABLES = (
    "memberships",
    "assistants",
    "assistant_config_versions",
    "channel_connections",
    "knowledge_sources",
    "knowledge_chunks",
    "knowledge_digests",
    "knowledge_gaps",
    "persons",
    "organisations",
    "person_org_roles",
    "identities",
    "identity_merge_log",
    "facts",
    "commitments",
    "edges",
    "embeddings",
    "conversations",
    "messages",
    "intakes",
    "intake_reviews",
    "tasks",
    "jobs",
    "audit_log",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inbound_app') THEN
                CREATE ROLE inbound_app NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    for name, values in (
        ("membership_role", "owner, staff"),
        ("channel_kind", "phone, gmail, whatsapp, web"),
        ("knowledge_gap_status", "open, answered, dismissed"),
        ("identity_type", "phone, email, whatsapp"),
        ("identity_merge_action", "merge, split"),
        ("fact_status", "active, superseded, rejected"),
        ("commitment_status", "open, kept, broken, cancelled"),
        ("conversation_channel", "phone, web, gmail, whatsapp"),
        ("conversation_status", "active, completed, failed"),
        ("message_role", "caller, assistant, operator, system"),
        ("intake_version", "live, post_call, merged"),
        ("task_status", "open, done, dismissed"),
        ("task_source", "live, post_call, manual"),
        ("job_status", "queued, running, succeeded, failed"),
    ):
        enum_values = ", ".join(f"'{value.strip()}'" for value in values.split(","))
        op.execute(f"CREATE TYPE {name} AS ENUM ({enum_values})")

    statements = [
        """
        CREATE TABLE tenants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email citext NOT NULL UNIQUE,
            password_hash text NOT NULL,
            display_name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE memberships (
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role membership_role NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )
        """,
        """
        CREATE TABLE assistants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name text NOT NULL,
            template text,
            config jsonb NOT NULL,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE assistant_config_versions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            version integer NOT NULL,
            config jsonb NOT NULL,
            created_by uuid NOT NULL REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, assistant_id, version)
        )
        """,
        """
        CREATE TABLE channel_connections (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            kind channel_kind NOT NULL,
            external_id text NOT NULL,
            status text NOT NULL,
            settings jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kind, external_id)
        )
        """,
        """
        CREATE TABLE knowledge_sources (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('url', 'file')),
            uri text NOT NULL,
            status text NOT NULL,
            manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
            fetched_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE knowledge_chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            chunk_index integer NOT NULL,
            title text NOT NULL,
            text text NOT NULL,
            sha256 varchar(64) NOT NULL,
            embedding vector(768),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, source_id, chunk_index)
        )
        """,
        """
        CREATE TABLE knowledge_digests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            content text NOT NULL,
            edited_by uuid REFERENCES users(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE knowledge_gaps (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL,
            question text NOT NULL,
            evidence text NOT NULL,
            status knowledge_gap_status NOT NULL DEFAULT 'open',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE persons (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            display_name text NOT NULL,
            merged_into_id uuid REFERENCES persons(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE organisations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name text NOT NULL,
            domain text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE person_org_roles (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            organisation_id uuid NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
            role text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, person_id, organisation_id, role)
        )
        """,
        """
        CREATE TABLE identities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            type identity_type NOT NULL,
            value_normalized text NOT NULL,
            verified boolean NOT NULL DEFAULT false,
            confidence real NOT NULL,
            source text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, type, value_normalized)
        )
        """,
        """
        CREATE TABLE identity_merge_log (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            action identity_merge_action NOT NULL,
            survivor_id uuid NOT NULL REFERENCES persons(id),
            merged_id uuid NOT NULL REFERENCES persons(id),
            actor_user_id uuid NOT NULL REFERENCES users(id),
            snapshot jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE facts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            subject_type text NOT NULL,
            subject_id uuid NOT NULL,
            predicate text NOT NULL,
            object_type text,
            object_id uuid,
            value_json jsonb,
            valid_from timestamptz NOT NULL,
            valid_to timestamptz,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            superseded_at timestamptz,
            source_conversation_id uuid,
            source_message_id uuid,
            evidence text NOT NULL,
            confidence real NOT NULL,
            status fact_status NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (object_id IS NOT NULL OR value_json IS NOT NULL)
        )
        """,
        """
        CREATE TABLE commitments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            what text NOT NULL,
            due_at timestamptz,
            status commitment_status NOT NULL DEFAULT 'open',
            source_message_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE edges (
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            src_type text NOT NULL,
            src_id uuid NOT NULL,
            rel text NOT NULL,
            dst_type text NOT NULL,
            dst_id uuid NOT NULL,
            valid_from timestamptz NOT NULL,
            valid_to timestamptz,
            source_message_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, src_type, src_id, rel, dst_type, dst_id, valid_from)
        )
        """,
        """
        CREATE TABLE embeddings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            owner_type text NOT NULL,
            owner_id uuid NOT NULL,
            embedding vector(768) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE conversations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id uuid NOT NULL REFERENCES assistants(id),
            person_id uuid REFERENCES persons(id) ON DELETE SET NULL,
            channel conversation_channel NOT NULL,
            external_ref text NOT NULL,
            status conversation_status NOT NULL,
            started_at timestamptz NOT NULL,
            ended_at timestamptz,
            summary text,
            end_reason text,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            estimated_cost jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, channel, external_ref)
        )
        """,
        """
        CREATE TABLE messages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role message_role NOT NULL,
            text text NOT NULL,
            ts timestamptz NOT NULL,
            meta jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE intakes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            version intake_version NOT NULL,
            data jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (conversation_id, version)
        )
        """,
        """
        CREATE TABLE intake_reviews (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            field text NOT NULL,
            resolved_value jsonb NOT NULL,
            resolved_by uuid NOT NULL REFERENCES users(id),
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE tasks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
            person_id uuid REFERENCES persons(id) ON DELETE SET NULL,
            kind text NOT NULL,
            status task_status NOT NULL DEFAULT 'open',
            due_at timestamptz,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            source task_source NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE jobs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind text NOT NULL,
            status job_status NOT NULL DEFAULT 'queued',
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            result jsonb,
            error text,
            attempts integer NOT NULL DEFAULT 0,
            run_after timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE audit_log (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            action text NOT NULL,
            target_type text NOT NULL,
            target_id uuid NOT NULL,
            diff jsonb NOT NULL DEFAULT '{}'::jsonb,
            ts timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        "ALTER TABLE knowledge_gaps ADD CONSTRAINT fk_knowledge_gaps_conversation "
        "FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE",
        "ALTER TABLE facts ADD CONSTRAINT fk_facts_source_conversation "
        "FOREIGN KEY (source_conversation_id) REFERENCES conversations(id) ON DELETE SET NULL",
        "ALTER TABLE facts ADD CONSTRAINT fk_facts_source_message "
        "FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE SET NULL",
        "ALTER TABLE commitments ADD CONSTRAINT fk_commitments_source_message "
        "FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE CASCADE",
        "ALTER TABLE edges ADD CONSTRAINT fk_edges_source_message "
        "FOREIGN KEY (source_message_id) REFERENCES messages(id) ON DELETE SET NULL",
    ]
    for statement in statements:
        op.execute(statement)

    for statement in (
        "CREATE INDEX ix_conversations_tenant_started ON conversations (tenant_id, started_at)",
        "CREATE INDEX ix_messages_tenant_conversation_ts "
        "ON messages (tenant_id, conversation_id, ts)",
        "CREATE INDEX ix_jobs_claim ON jobs (status, run_after)",
        """
        CREATE FUNCTION resolve_channel(p_kind channel_kind, p_external_id text)
        RETURNS TABLE (tenant_id uuid, assistant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT connection.tenant_id, connection.assistant_id
            FROM channel_connections AS connection
            WHERE connection.kind = p_kind AND connection.external_id = p_external_id
        $$
        """,
        """
        CREATE FUNCTION lookup_user_memberships(p_email citext)
        RETURNS TABLE (user_id uuid, tenant_id uuid, role membership_role)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT users.id, memberships.tenant_id, memberships.role
            FROM users
            JOIN memberships ON memberships.user_id = users.id
            WHERE users.email = p_email
        $$
        """,
        "REVOKE ALL ON FUNCTION resolve_channel(channel_kind, text) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION lookup_user_memberships(citext) FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION resolve_channel(channel_kind, text) TO inbound_app",
        "GRANT EXECUTE ON FUNCTION lookup_user_memberships(citext) TO inbound_app",
        "GRANT USAGE ON SCHEMA public TO inbound_app",
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO inbound_app",
        "GRANT inbound_app TO inbound_app_login",
        """
        DO $$
        DECLARE
            table_name text;
        BEGIN
            FOREACH table_name IN ARRAY ARRAY[
                'memberships', 'assistants', 'assistant_config_versions',
                'channel_connections', 'knowledge_sources', 'knowledge_chunks',
                'knowledge_digests', 'knowledge_gaps', 'persons', 'organisations',
                'person_org_roles', 'identities', 'identity_merge_log', 'facts',
                'commitments', 'edges', 'embeddings', 'conversations', 'messages',
                'intakes', 'intake_reviews', 'tasks', 'jobs', 'audit_log'
            ]
            LOOP
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
                EXECUTE format(
                    'CREATE POLICY tenant_isolation ON %I FOR ALL '
                    'USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid) '
                    'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid)',
                    table_name
                );
            END LOOP;
        END
        $$;
        """,
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_user_memberships(citext)")
    op.execute("DROP FUNCTION IF EXISTS resolve_channel(channel_kind, text)")
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for enum_name in (
        "job_status",
        "task_source",
        "task_status",
        "intake_version",
        "message_role",
        "conversation_status",
        "conversation_channel",
        "commitment_status",
        "fact_status",
        "identity_merge_action",
        "identity_type",
        "knowledge_gap_status",
        "channel_kind",
        "membership_role",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
