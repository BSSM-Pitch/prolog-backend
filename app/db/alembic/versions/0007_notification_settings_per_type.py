"""알림 설정을 유형별 행으로 — 화면 31 · NOTI 명세 §2.2·§4.6.

0001 은 사용자당 한 행(`in_app_enabled` · `email_enabled` · `muted_types[]`)이었다. 화면 31 은
유형마다 "서비스 알림 / 이메일" 을 따로 켜고 끈다(명세 §2.2 `NotificationSetting[]` 와 같다).
한 행 + 음소거 배열로는 "멘션은 앱으로만, 이메일은 끔" 을 표현할 수 없다.

행은 **기본값에서 바꾼 것만** 둔다. 행이 없는 유형은 둘 다 켜짐(명세 기본값 true).
기존 행은 버리지 않고 유형마다 펼친다(`muted_types` 에 든 유형은 in_app 끔).

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES = "'team_invite','project_invite','team_joined','mention','system'"

UPGRADE = f"""
ALTER TABLE platform.notification_settings RENAME TO notification_settings_old;
ALTER TABLE platform.notification_settings_old
    RENAME CONSTRAINT notification_settings_pkey TO notification_settings_old_pkey;
CREATE TABLE platform.notification_settings (
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    type varchar(50) NOT NULL,
    in_app_enabled boolean NOT NULL DEFAULT true,
    email_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, type),
    CONSTRAINT notification_settings_type_chk CHECK (type IN ({TYPES}))
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON platform.notification_settings
    FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

INSERT INTO platform.notification_settings (user_id, type, in_app_enabled, email_enabled)
SELECT s.user_id, t.type,
       s.in_app_enabled AND NOT (t.type = ANY(s.muted_types)),
       s.email_enabled
  FROM platform.notification_settings_old s
 CROSS JOIN unnest(ARRAY[{TYPES}]) AS t(type)
 WHERE NOT s.in_app_enabled OR NOT s.email_enabled OR cardinality(s.muted_types) > 0;

DROP TABLE platform.notification_settings_old;
"""

DOWNGRADE = """
ALTER TABLE platform.notification_settings RENAME TO notification_settings_new;
ALTER TABLE platform.notification_settings_new
    RENAME CONSTRAINT notification_settings_pkey TO notification_settings_new_pkey;
CREATE TABLE platform.notification_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL UNIQUE REFERENCES platform.users(id) ON DELETE CASCADE,
    in_app_enabled boolean NOT NULL DEFAULT true,
    email_enabled boolean NOT NULL DEFAULT true,
    muted_types text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON platform.notification_settings
    FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
-- 유형별 이메일 설정은 한 행으로 접으면 사라진다: 하나라도 끈 유형이 있으면 email 끔.
INSERT INTO platform.notification_settings (user_id, email_enabled, muted_types)
SELECT user_id, bool_and(email_enabled),
       coalesce(array_agg(type) FILTER (WHERE NOT in_app_enabled), '{}')
  FROM platform.notification_settings_new GROUP BY user_id;
DROP TABLE platform.notification_settings_new;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
