from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk


class User(Base, Timestamps):
    __tablename__ = "users"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    provider_user_id: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))
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


class PasswordReset(Base, Timestamps):
    __tablename__ = "password_resets"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID] = fk_uuid()
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
