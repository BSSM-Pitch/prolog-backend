from typing import Any


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """성공 응답 래퍼: {"data": ..., "meta": ...} (CLAUDE.md §3)."""
    return {"data": data, "meta": meta or {}}
