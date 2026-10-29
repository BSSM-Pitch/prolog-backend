"""이메일 인증 코드 — 일반 가입 인증과 비밀번호 재설정을 한 테이블로.

코드를 보낼 때마다 한 행. 원문 코드는 저장하지 않고 sha256 만 둔다(초대 token_hash 와 같다).
가입 전에는 사용자가 없으므로 user_id 가 아니라 이메일로 찾는다 — 가장 최근 행만 유효하다.

- `attempts`: 6자리 코드는 100만 가지라 시도 횟수를 막지 않으면 맞춰 볼 수 있다. 상한은 앱이 정한다.
- `used_at`: 한 번 쓴 코드는 다시 못 쓴다.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform.email_verifications (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email varchar(255) NOT NULL,
            purpose varchar(20) NOT NULL,
            code_hash varchar(64) NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT email_verifications_purpose_chk
                CHECK (purpose IN ('signup','password_reset')),
            CONSTRAINT email_verifications_attempts_chk CHECK (attempts >= 0)
        );
        -- '이 이메일·용도의 가장 최근 코드' 조회와 발송 간격 제한(최근 N분 발송 수)이 탄다.
        CREATE INDEX email_verifications_lookup_idx
            ON platform.email_verifications (lower(email), purpose, created_at DESC);
        CREATE TRIGGER touch_updated_at BEFORE UPDATE ON platform.email_verifications
            FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE platform.email_verifications")
