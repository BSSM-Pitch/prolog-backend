from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk

# IntegrityError 는 제약 **이름**으로 분기한다. 메시지 파싱은 PG 마이너 버전에 깨진다(CLAUDE.md §5).
USERNAME_UQ = "users_username_uq"
PROVIDER_UQ = "users_provider_uq"


class User(Base, Timestamps):
    __tablename__ = "users"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    email: Mapped[str | None] = mapped_column(String(255))
    # v0.2: Google 단일. 비밀번호 컬럼은 존재하지 않는다 (CLAUDE.md §2 규칙 4).
    auth_provider: Mapped[str] = mapped_column(String(20), default="google")
    # Google `sub`. 이메일이 아니다 — 이메일은 바뀌고 sub 는 불변이다 (AUTH 명세 §2.1).
    provider_user_id: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(30))
    # users.role 은 자기 분류다. 인가와 무관하다 (CLAUDE.md §3).
    role: Mapped[str] = mapped_column(String(20))
    # ASSUMPTION: 명세 미정의. 요금제 도입 시 CHECK 값만 확장 (CLAUDE.md §5).
    plan: Mapped[str] = mapped_column(String(20), default="free")


class RefreshToken(Base, Timestamps):
    __tablename__ = "refresh_tokens"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID] = fk_uuid()
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
