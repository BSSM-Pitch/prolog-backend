from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, pk


class OutboxEvent(Base, Timestamps):
    """도메인 변경과 같은 트랜잭션에서 INSERT 된다 (CLAUDE.md §8).

    payload 는 자기완결적으로 채운다 — 핸들러가 원본 테이블을 다시 읽지 않아도 되게.
    """

    __tablename__ = "outbox_events"
    __table_args__ = {"schema": "ops"}

    id: Mapped[UUID] = pk()
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
