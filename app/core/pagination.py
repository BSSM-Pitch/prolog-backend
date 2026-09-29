"""커서 기반 페이지네이션 (CLAUDE.md §3).

커서는 (정렬키, id) 키셋이다. offset 을 쓰지 않으므로 삽입 중에도 중복/누락이 없다.
정렬키는 대부분 `created_at` 이고, 멤버는 `joined_at`, 챕터는 `chapter_no` 다.
"""

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Query

from app.core.errors import InvalidInput

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

Limit = Annotated[
    int,
    Query(ge=1, le=MAX_LIMIT, description=f"페이지 크기. 기본 {DEFAULT_LIMIT} · 최대 {MAX_LIMIT}"),
]
Cursor = Annotated[
    str | None, Query(description="이전 응답의 `meta.next_cursor`. 첫 페이지는 비운다")
]

SortKey = datetime | int


def encode_cursor(key: SortKey, row_id: UUID) -> str:
    raw = f"{key.isoformat() if isinstance(key, datetime) else key}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode[K](cursor: str, parse: Callable[[str], K]) -> tuple[K, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        key, _, row_id = base64.urlsafe_b64decode(padded).decode().partition("|")
        return parse(key), UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInput("cursor 형식이 올바르지 않습니다", field="cursor") from exc


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    return _decode(cursor, datetime.fromisoformat)


def decode_int_cursor(cursor: str) -> tuple[int, UUID]:
    return _decode(cursor, int)


def _created(row: Any) -> tuple[SortKey, UUID]:
    return row.created_at, row.id


def next_cursor[T](
    rows: list[T], limit: int, key: Callable[[T], tuple[SortKey, UUID]] = _created
) -> tuple[list[T], dict[str, Any]]:
    """limit+1 건 조회 결과를 (페이지, meta) 로 자른다. `key` 는 정렬에 쓴 (키, id) 다."""
    if len(rows) <= limit:
        return rows, {"next_cursor": None}
    page = rows[:limit]
    return page, {"next_cursor": encode_cursor(*key(page[-1]))}
