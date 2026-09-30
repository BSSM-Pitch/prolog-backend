from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, fk_uuid, pk

# 내부 status 는 이 5개만 존재한다 (CLAUDE.md §6). 응답 별칭 변환은 직렬화 계층에서만 한다.
STATUSES = ("queued", "running", "completed", "failed", "skipped")


class Job(Base, Timestamps):
    __tablename__ = "jobs"
    __table_args__ = {"schema": "ops"}

    id: Mapped[UUID] = pk()  # API 의 extraction_id / analysis_id / check_id 전부
    project_id: Mapped[UUID] = fk_uuid()
    job_type: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    queue: Mapped[str] = mapped_column(String(10))  # ai | io
    status: Mapped[str] = mapped_column(String(20), default="queued")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempt: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AI 잡이 도는 동안 워커가 찍는다(0011). 좀비 판정은 이것(없으면 started_at) 기준이다.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
