from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk

# IntegrityError 는 제약 이름으로 분기한다(CLAUDE.md §7).
PROJECT_INVITATIONS_PENDING_UQ = "project_invitations_pending_uq"


class Project(Base, Timestamps):
    __tablename__ = "projects"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    owner_type: Mapped[str] = mapped_column(String(20))  # personal | team
    team_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_by: Mapped[UUID] = fk_uuid()


class ProjectMember(Base, Timestamps):
    __tablename__ = "project_members"
    __table_args__ = {"schema": "platform"}

    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))  # owner | editor | viewer
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectInvitation(Base, Timestamps):
    __tablename__ = "project_invitations"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = pk()
    project_id: Mapped[UUID] = fk_uuid()
    invited_email: Mapped[str] = mapped_column(String(255))
    invited_by: Mapped[UUID] = fk_uuid()
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
