"""일반(아이디·비밀번호) 로그인 + 네이버 — v0.2 의 Google 단일 방침을 되돌린다.

`auth_provider = 'local'` 계정만 `password_hash` 를 갖는다. Google 계정은 비밀번호가 없다.
local 의 `provider_user_id` 는 username 이다(둘 다 UNIQUE 라 판별이 겹치지 않는다).
naver 는 프로필 `id` 다.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.users ADD COLUMN password_hash varchar(255);
        ALTER TABLE platform.users DROP CONSTRAINT users_auth_provider_chk;
        ALTER TABLE platform.users ADD CONSTRAINT users_auth_provider_chk
            CHECK (auth_provider IN ('google','local','naver'));
        ALTER TABLE platform.users ADD CONSTRAINT users_local_password_chk
            CHECK ((auth_provider = 'local') = (password_hash IS NOT NULL));
        """
    )


def downgrade() -> None:
    # local 계정이 남아 있으면 CHECK 재생성에서 실패한다 — 계정을 조용히 지우지 않는다.
    op.execute(
        """
        ALTER TABLE platform.users DROP CONSTRAINT users_local_password_chk;
        ALTER TABLE platform.users DROP CONSTRAINT users_auth_provider_chk;
        ALTER TABLE platform.users ADD CONSTRAINT users_auth_provider_chk
            CHECK (auth_provider IN ('google'));
        ALTER TABLE platform.users DROP COLUMN password_hash;
        """
    )
