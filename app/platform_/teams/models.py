from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk

# IntegrityError 는 제약 이름으로 분기한다(CLAUDE.md §7).
TEAM_INVITATIONS_PENDING_UQ = "team_invitations_pending_uq"
# platform.projects 의 FK (PG 자동 명명). teams 는 projects 를 import 하지 않으므로
# 이름만 문자열로 둔다.
PROJECTS_TEAM_FK = "projects_team_id_fkey"


class Team(Base, Timestamps):
    __tablename__ = "teams"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = fk_uuid()


class TeamMember(Base, Timestamps):
    __tablename__ = "team_members"
    __table_args__ = {"schema": "platform"}

    # 조인 테이블만 복합 PK (CLAUDE.md §5)
    team_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamInvitation(Base, Timestamps):
    __tablename__ = "team_invitations"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    team_id: Mapped[UUID] = fk_uuid()
    invited_email: Mapped[str] = mapped_column(String(255))
    invited_by: Mapped[UUID] = fk_uuid()
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 초대 링크는 랜덤 값의 sha256 만 저장한다. 원문은 생성 응답에만 나간다 (CLAUDE.md §6.4).
    token_hash: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
