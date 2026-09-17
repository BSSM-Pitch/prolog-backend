"""37 테이블 초기 스키마 (CLAUDE.md §5).

DDL 을 raw SQL 로 쓴다. 부분 인덱스 · lower() UNIQUE · CHECK · 트리거는
autogenerate 가 만들어내지 못하므로, 이 파일이 스키마의 정본이다.
모델과의 정합성은 tests/test_schema_matches_models.py 가 검사한다.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = """
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
"""

UPGRADE = f"""
CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS content;
CREATE SCHEMA IF NOT EXISTS authoring;
CREATE SCHEMA IF NOT EXISTS insight;
CREATE SCHEMA IF NOT EXISTS ops;

-- ========================= platform (11) =========================
CREATE TABLE platform.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email varchar(255),
    auth_provider varchar(20) NOT NULL DEFAULT 'google',
    provider_user_id varchar(255) NOT NULL,
    username varchar(30) NOT NULL,
    role varchar(20) NOT NULL,
    plan varchar(20) NOT NULL DEFAULT 'free',
    {TS},
    CONSTRAINT users_auth_provider_chk CHECK (auth_provider IN ('google')),
    CONSTRAINT users_role_chk CHECK (role IN ('writer','aspiring_writer','reader')),
    CONSTRAINT users_plan_chk CHECK (plan IN ('free')),
    CONSTRAINT users_username_uq UNIQUE (username)
);
CREATE UNIQUE INDEX users_provider_uq ON platform.users (auth_provider, provider_user_id);
CREATE UNIQUE INDEX users_email_lower_uq ON platform.users (lower(email))
    WHERE email IS NOT NULL;

CREATE TABLE platform.refresh_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    token_hash varchar(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    {TS}
);
CREATE INDEX refresh_tokens_user_idx ON platform.refresh_tokens (user_id);

CREATE TABLE platform.teams (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    description text,
    created_by uuid NOT NULL REFERENCES platform.users(id) ON DELETE RESTRICT,
    {TS}
);

CREATE TABLE platform.team_members (
    team_id uuid NOT NULL REFERENCES platform.teams(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    role varchar(20) NOT NULL,
    joined_at timestamptz NOT NULL DEFAULT now(),
    {TS},
    PRIMARY KEY (team_id, user_id),
    CONSTRAINT team_members_role_chk CHECK (role IN ('owner','admin','member'))
);
CREATE INDEX team_members_user_idx ON platform.team_members (user_id);

CREATE TABLE platform.team_invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id uuid NOT NULL REFERENCES platform.teams(id) ON DELETE CASCADE,
    invited_email varchar(255) NOT NULL,
    invited_by uuid NOT NULL REFERENCES platform.users(id) ON DELETE RESTRICT,
    role varchar(20) NOT NULL DEFAULT 'member',
    status varchar(20) NOT NULL DEFAULT 'pending',
    expires_at timestamptz NOT NULL,
    responded_at timestamptz,
    {TS},
    CONSTRAINT team_invitations_role_chk CHECK (role IN ('admin','member')),
    CONSTRAINT team_invitations_status_chk
        CHECK (status IN ('pending','accepted','rejected','revoked','expired'))
);
CREATE UNIQUE INDEX team_invitations_pending_uq
    ON platform.team_invitations (team_id, lower(invited_email)) WHERE status = 'pending';
CREATE INDEX team_invitations_email_idx ON platform.team_invitations (lower(invited_email));

CREATE TABLE platform.projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title varchar(100) NOT NULL,
    description text,
    owner_type varchar(20) NOT NULL,
    team_id uuid REFERENCES platform.teams(id) ON DELETE RESTRICT,
    created_by uuid NOT NULL REFERENCES platform.users(id) ON DELETE RESTRICT,
    {TS},
    CONSTRAINT projects_owner_type_chk CHECK (owner_type IN ('personal','team')),
    CONSTRAINT projects_team_required_chk
        CHECK (owner_type <> 'team' OR team_id IS NOT NULL),
    CONSTRAINT projects_personal_no_team_chk
        CHECK (owner_type <> 'personal' OR team_id IS NULL)
);
CREATE INDEX projects_team_idx ON platform.projects (team_id);

CREATE TABLE platform.project_members (
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    role varchar(20) NOT NULL,
    joined_at timestamptz NOT NULL DEFAULT now(),
    {TS},
    PRIMARY KEY (project_id, user_id),
    CONSTRAINT project_members_role_chk CHECK (role IN ('owner','editor','viewer'))
);
CREATE INDEX project_members_user_idx ON platform.project_members (user_id);

CREATE TABLE platform.project_invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    invited_email varchar(255) NOT NULL,
    invited_by uuid NOT NULL REFERENCES platform.users(id) ON DELETE RESTRICT,
    role varchar(20) NOT NULL DEFAULT 'editor',
    status varchar(20) NOT NULL DEFAULT 'pending',
    expires_at timestamptz NOT NULL,
    responded_at timestamptz,
    {TS},
    CONSTRAINT project_invitations_role_chk CHECK (role IN ('editor','viewer')),
    CONSTRAINT project_invitations_status_chk
        CHECK (status IN ('pending','accepted','rejected','revoked','expired'))
);
CREATE UNIQUE INDEX project_invitations_pending_uq
    ON platform.project_invitations (project_id, lower(invited_email)) WHERE status = 'pending';
CREATE INDEX project_invitations_email_idx
    ON platform.project_invitations (lower(invited_email));

CREATE TABLE platform.notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    type varchar(50) NOT NULL,
    title varchar(200) NOT NULL,
    body text,
    payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    resource_type varchar(50),
    resource_id uuid,
    channels_sent text[] NOT NULL DEFAULT '{{}}',
    read_at timestamptz,
    {TS}
);
CREATE INDEX notifications_user_unread_idx
    ON platform.notifications (user_id, created_at DESC) WHERE read_at IS NULL;

CREATE TABLE platform.notification_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL UNIQUE REFERENCES platform.users(id) ON DELETE CASCADE,
    in_app_enabled boolean NOT NULL DEFAULT true,
    email_enabled boolean NOT NULL DEFAULT true,
    muted_types text[] NOT NULL DEFAULT '{{}}',
    {TS}
);

CREATE TABLE platform.email_integrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    provider varchar(30) NOT NULL,
    email varchar(255) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending',
    verified_at timestamptz,
    {TS},
    CONSTRAINT email_integrations_status_chk
        CHECK (status IN ('pending','verified','failed','disabled'))
);

-- ========================= content (3) =========================
CREATE TABLE content.manuscripts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    title varchar(200) NOT NULL,
    source_type varchar(20) NOT NULL,
    file_key varchar(512),
    content text,
    status varchar(20) NOT NULL DEFAULT 'draft',
    extraction_job_id uuid,
    {TS},
    CONSTRAINT manuscripts_source_type_chk CHECK (source_type IN ('editor','upload')),
    CONSTRAINT manuscripts_status_chk
        CHECK (status IN ('draft','processing','ready','failed')),
    CONSTRAINT manuscripts_editor_no_file_chk
        CHECK (source_type <> 'editor' OR file_key IS NULL),
    CONSTRAINT manuscripts_ready_content_chk
        CHECK (status <> 'ready' OR content IS NOT NULL)
);
CREATE INDEX manuscripts_project_idx ON content.manuscripts (project_id);

CREATE TABLE content.chapters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    manuscript_id uuid NOT NULL REFERENCES content.manuscripts(id) ON DELETE CASCADE,
    chapter_no integer NOT NULL,
    title varchar(200),
    content text NOT NULL DEFAULT '',
    {TS},
    CONSTRAINT chapters_manuscript_no_uq UNIQUE (manuscript_id, chapter_no)
);
CREATE INDEX chapters_project_no_idx ON content.chapters (project_id, chapter_no);

CREATE TABLE content.manuscript_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    manuscript_id uuid NOT NULL REFERENCES content.manuscripts(id) ON DELETE CASCADE,
    chapter_id uuid REFERENCES content.chapters(id) ON DELETE CASCADE,
    version_no integer NOT NULL,
    content text NOT NULL,
    char_count integer NOT NULL DEFAULT 0,
    created_by uuid,
    {TS}
);
CREATE INDEX manuscript_versions_manuscript_idx
    ON content.manuscript_versions (manuscript_id, version_no DESC);

-- ========================= authoring (6) =========================
CREATE TABLE authoring.character_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    job_id uuid,
    source_text text NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending',
    created_by uuid,
    {TS},
    CONSTRAINT character_drafts_status_chk
        CHECK (status IN ('pending','confirmed','discarded'))
);
CREATE INDEX character_drafts_project_idx ON authoring.character_drafts (project_id);

CREATE TABLE authoring.characters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    name varchar(100) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'active',
    role varchar(50),
    description text,
    {TS},
    CONSTRAINT characters_status_chk CHECK (status IN ('active','archived'))
);
CREATE UNIQUE INDEX characters_project_name_active_uq
    ON authoring.characters (project_id, lower(name)) WHERE status = 'active';

CREATE TABLE authoring.character_draft_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    draft_id uuid NOT NULL REFERENCES authoring.character_drafts(id) ON DELETE CASCADE,
    name varchar(100) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    selected boolean NOT NULL DEFAULT false,
    character_id uuid REFERENCES authoring.characters(id) ON DELETE SET NULL,
    {TS}
);
CREATE INDEX character_draft_items_draft_idx ON authoring.character_draft_items (draft_id);

CREATE TABLE authoring.character_attributes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    character_id uuid NOT NULL REFERENCES authoring.characters(id) ON DELETE CASCADE,
    category varchar(30) NOT NULL,
    value varchar(100) NOT NULL,
    {TS},
    CONSTRAINT character_attributes_category_chk
        CHECK (category IN ('personality_tag','core_value'))
);
CREATE UNIQUE INDEX character_attributes_value_uq
    ON authoring.character_attributes (character_id, category, lower(value));
CREATE INDEX character_attributes_core_value_idx
    ON authoring.character_attributes (character_id) WHERE category = 'core_value';

CREATE TABLE authoring.character_edit_histories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    character_id uuid NOT NULL REFERENCES authoring.characters(id) ON DELETE CASCADE,
    edited_by uuid,
    before jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    after jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    {TS}
);
CREATE INDEX character_edit_histories_character_idx
    ON authoring.character_edit_histories (character_id, created_at DESC);

CREATE TABLE authoring.world_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    title varchar(200) NOT NULL,
    description text,
    category varchar(50),
    violation_keywords text[] NOT NULL DEFAULT '{{}}',
    origin varchar(20) NOT NULL DEFAULT 'manual',
    evidence text,
    extraction_job_id uuid,
    source_chapter_no integer,
    {TS},
    CONSTRAINT world_rules_origin_chk CHECK (origin IN ('manual','ai'))
);
CREATE INDEX world_rules_project_idx ON authoring.world_rules (project_id);

-- ========================= insight (15) =========================
CREATE TABLE insight.events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    chapter_id uuid NOT NULL REFERENCES content.chapters(id) ON DELETE CASCADE,
    description text NOT NULL,
    emotion_keywords text[] NOT NULL DEFAULT '{{}}',
    occurred_order integer,
    created_by uuid,
    {TS}
);
CREATE INDEX events_chapter_idx ON insight.events (chapter_id, occurred_order);
CREATE INDEX events_project_idx ON insight.events (project_id);

CREATE TABLE insight.event_characters (
    event_id uuid NOT NULL REFERENCES insight.events(id) ON DELETE CASCADE,
    character_id uuid NOT NULL REFERENCES authoring.characters(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    role varchar(30),
    {TS},
    PRIMARY KEY (event_id, character_id)
);
CREATE INDEX event_characters_character_idx ON insight.event_characters (character_id);

CREATE TABLE insight.conflicts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    event_id uuid NOT NULL REFERENCES insight.events(id) ON DELETE CASCADE,
    character_id uuid REFERENCES authoring.characters(id) ON DELETE CASCADE,
    rule_id uuid REFERENCES authoring.world_rules(id) ON DELETE CASCADE,
    detected_by varchar(20) NOT NULL DEFAULT 'rule',
    severity varchar(20),
    status varchar(20) NOT NULL DEFAULT 'open',
    advice text,
    suppression_key varchar(64) NOT NULL,
    job_id uuid,
    {TS},
    CONSTRAINT conflicts_detected_by_chk CHECK (detected_by IN ('rule','ai')),
    CONSTRAINT conflicts_status_chk
        CHECK (status IN ('open','accepted','ignored','modified')),
    CONSTRAINT conflicts_severity_chk
        CHECK (severity IS NULL OR severity IN ('low','medium','high'))
);
CREATE INDEX conflicts_event_idx ON insight.conflicts (event_id);
CREATE INDEX conflicts_project_open_idx
    ON insight.conflicts (project_id, created_at DESC) WHERE status = 'open';

CREATE TABLE insight.conflict_suppressions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    suppression_key varchar(64) NOT NULL UNIQUE,
    created_by uuid,
    {TS}
);

CREATE TABLE insight.relationships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    source_character_id uuid NOT NULL
        REFERENCES authoring.characters(id) ON DELETE RESTRICT,
    target_character_id uuid NOT NULL
        REFERENCES authoring.characters(id) ON DELETE RESTRICT,
    type varchar(50),
    {TS},
    CONSTRAINT relationships_pair_uq
        UNIQUE (project_id, source_character_id, target_character_id),
    CONSTRAINT relationships_no_self_chk
        CHECK (source_character_id <> target_character_id)
);

CREATE TABLE insight.relationship_histories (
    relationship_id uuid NOT NULL REFERENCES insight.relationships(id) ON DELETE CASCADE,
    chapter_id uuid NOT NULL REFERENCES content.chapters(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    chapter_no integer NOT NULL,
    state varchar(50),
    trust integer,
    note text,
    {TS},
    PRIMARY KEY (relationship_id, chapter_id)
);
CREATE INDEX relationship_histories_carry_idx
    ON insight.relationship_histories (relationship_id, chapter_no DESC);
CREATE INDEX relationship_histories_project_idx
    ON insight.relationship_histories (project_id, chapter_no);

CREATE TABLE insight.foreshadowings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    title varchar(200) NOT NULL,
    description text,
    status varchar(20) NOT NULL DEFAULT 'planted',
    setup_chapter_no integer,
    payoff_chapter_no integer,
    {TS},
    CONSTRAINT foreshadowings_status_chk
        CHECK (status IN ('planted','resolved','abandoned')),
    CONSTRAINT foreshadowings_payoff_order_chk
        CHECK (payoff_chapter_no IS NULL OR setup_chapter_no IS NULL
               OR payoff_chapter_no >= setup_chapter_no)
);
CREATE INDEX foreshadowings_project_idx ON insight.foreshadowings (project_id);

CREATE TABLE insight.foreshadowing_chapters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    foreshadowing_id uuid NOT NULL
        REFERENCES insight.foreshadowings(id) ON DELETE CASCADE,
    chapter_id uuid NOT NULL REFERENCES content.chapters(id) ON DELETE RESTRICT,
    role varchar(20) NOT NULL,
    {TS},
    CONSTRAINT foreshadowing_chapters_role_chk CHECK (role IN ('setup','payoff','hint')),
    CONSTRAINT foreshadowing_chapters_uq UNIQUE (foreshadowing_id, chapter_id, role)
);
CREATE UNIQUE INDEX foreshadowing_chapters_setup_uq
    ON insight.foreshadowing_chapters (foreshadowing_id) WHERE role = 'setup';
CREATE UNIQUE INDEX foreshadowing_chapters_payoff_uq
    ON insight.foreshadowing_chapters (foreshadowing_id) WHERE role = 'payoff';
CREATE INDEX foreshadowing_chapters_chapter_idx
    ON insight.foreshadowing_chapters (chapter_id);

CREATE TABLE insight.foreshadowing_events (
    foreshadowing_id uuid NOT NULL
        REFERENCES insight.foreshadowings(id) ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES insight.events(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    {TS},
    PRIMARY KEY (foreshadowing_id, event_id)
);

CREATE TABLE insight.foreshadowing_characters (
    foreshadowing_id uuid NOT NULL
        REFERENCES insight.foreshadowings(id) ON DELETE CASCADE,
    character_id uuid NOT NULL REFERENCES authoring.characters(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    {TS},
    PRIMARY KEY (foreshadowing_id, character_id)
);

CREATE TABLE insight.qa_threads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    manuscript_id uuid REFERENCES content.manuscripts(id) ON DELETE CASCADE,
    chapter_id uuid REFERENCES content.chapters(id) ON DELETE CASCADE,
    scope varchar(20) NOT NULL,
    selection_start integer,
    selection_end integer,
    selected_text text,
    title varchar(200),
    created_by uuid,
    {TS},
    CONSTRAINT qa_threads_scope_chk CHECK (scope IN ('project','chapter','selection')),
    CONSTRAINT qa_threads_selection_chk
        CHECK (scope <> 'selection'
               OR (selection_start IS NOT NULL AND selection_end > selection_start))
);
CREATE INDEX qa_threads_project_idx ON insight.qa_threads (project_id, created_at DESC);

CREATE TABLE insight.qa_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    thread_id uuid NOT NULL REFERENCES insight.qa_threads(id) ON DELETE CASCADE,
    role varchar(20) NOT NULL,
    content text,
    job_id uuid,
    status varchar(20),
    {TS},
    CONSTRAINT qa_messages_role_chk CHECK (role IN ('user','assistant'))
);
CREATE INDEX qa_messages_thread_idx ON insight.qa_messages (thread_id, created_at);

CREATE TABLE insight.structure_maps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    manuscript_id uuid NOT NULL UNIQUE
        REFERENCES content.manuscripts(id) ON DELETE CASCADE,
    acts jsonb NOT NULL DEFAULT '[]'::jsonb,
    edges jsonb NOT NULL DEFAULT '[]'::jsonb,
    job_id uuid,
    {TS}
);

CREATE TABLE insight.structure_nodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    map_id uuid NOT NULL REFERENCES insight.structure_maps(id) ON DELETE CASCADE,
    node_type varchar(30) NOT NULL,
    title varchar(200) NOT NULL,
    summary text,
    chapter_no integer,
    position jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    is_user_edited boolean NOT NULL DEFAULT false,
    {TS}
);
CREATE INDEX structure_nodes_map_idx ON insight.structure_nodes (map_id);

CREATE TABLE insight.structure_node_characters (
    node_id uuid NOT NULL REFERENCES insight.structure_nodes(id) ON DELETE CASCADE,
    character_id uuid NOT NULL REFERENCES authoring.characters(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    {TS},
    PRIMARY KEY (node_id, character_id)
);

-- ========================= ops (2) =========================
CREATE TABLE ops.jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES platform.projects(id) ON DELETE CASCADE,
    job_type varchar(40) NOT NULL,
    target_type varchar(30) NOT NULL,
    target_id uuid,
    queue varchar(10) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'queued',
    input jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    result jsonb,
    error jsonb,
    attempt integer NOT NULL DEFAULT 0,
    max_attempt integer NOT NULL DEFAULT 3,
    idempotency_key varchar(128) UNIQUE,
    started_at timestamptz,
    finished_at timestamptz,
    created_by uuid,
    {TS},
    CONSTRAINT jobs_status_chk
        CHECK (status IN ('queued','running','completed','failed','skipped')),
    CONSTRAINT jobs_queue_chk CHECK (queue IN ('ai','io')),
    CONSTRAINT jobs_job_type_chk CHECK (job_type IN (
        'nl_extraction','rule_extraction','structure_analysis',
        'conflict_check','qa_answer','manuscript_extraction')),
    CONSTRAINT jobs_completed_result_chk
        CHECK (status <> 'completed' OR result IS NOT NULL),
    CONSTRAINT jobs_failed_error_chk CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX jobs_project_idx ON ops.jobs (project_id, created_at DESC);
CREATE INDEX jobs_target_idx ON ops.jobs (target_type, target_id);
CREATE INDEX jobs_running_idx ON ops.jobs (status, started_at) WHERE status = 'running';

CREATE TABLE ops.outbox_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type varchar(50) NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type varchar(50) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    published_at timestamptz,
    {TS}
);
CREATE INDEX outbox_events_unpublished_idx
    ON ops.outbox_events (created_at) WHERE published_at IS NULL;

-- ========================= 트리거 =========================
CREATE FUNCTION ops.touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t record;
BEGIN
    FOR t IN
        SELECT table_schema, table_name FROM information_schema.columns
         WHERE column_name = 'updated_at'
           AND table_schema IN ('platform','content','authoring','insight','ops')
    LOOP
        EXECUTE 'CREATE TRIGGER touch_updated_at BEFORE UPDATE ON '
             || quote_ident(t.table_schema) || '.' || quote_ident(t.table_name)
             || ' FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at()';
    END LOOP;
END $$;

-- manuscripts.source_type 은 생성 후 불변 → SOURCE_TYPE_IMMUTABLE (409)
CREATE FUNCTION content.manuscripts_source_type_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.source_type <> OLD.source_type THEN
        RAISE EXCEPTION 'SOURCE_TYPE_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER manuscripts_source_type_immutable
    BEFORE UPDATE ON content.manuscripts
    FOR EACH ROW EXECUTE FUNCTION content.manuscripts_source_type_immutable();
"""

DOWNGRADE = """
DROP SCHEMA IF EXISTS insight CASCADE;
DROP SCHEMA IF EXISTS authoring CASCADE;
DROP SCHEMA IF EXISTS content CASCADE;
DROP SCHEMA IF EXISTS ops CASCADE;
DROP SCHEMA IF EXISTS platform CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
