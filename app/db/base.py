"""SQLAlchemy 선언 기반 + 공통 믹스인.

DDL 정본은 ``app/db/alembic/versions/0001_initial.py`` 다.
부분 인덱스 · CHECK · lower() UNIQUE 는 autogenerate 가 놓치므로 마이그레이션에만 있다.
모델은 컬럼(=파이썬이 읽고 쓰는 것)만 선언한다.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def fk_uuid() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), nullable=False)


class Timestamps:
    """모든 엔티티 필수 (CLAUDE.md §5). updated_at 은 DB 트리거가 갱신한다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
