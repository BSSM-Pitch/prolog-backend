"""응답 봉투와 에러 문서화.

성공은 `{"data": ..., "meta": ...}`, 실패는 `{"error": {"code", "message", "details"}}` 다
(CLAUDE.md §3). 라우터는 `response_model=Envelope[X]` / `Page[X]` 로 성공 스키마를,
`responses=raises(...)` 로 그 엔드포인트가 **직접** 낼 수 있는 도메인 에러를 선언한다.
인증·권한·입력 검증처럼 의존성이 내는 에러는 `app.core.openapi` 가 의존성 그래프에서
자동으로 붙인다 — 엔드포인트마다 손으로 적으면 반드시 빠진다.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import AppError


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """성공 응답 래퍼: {"data": ..., "meta": ...} (CLAUDE.md §3)."""
    return {"data": data, "meta": meta or {}}


class Envelope[T](BaseModel):
    data: T
    meta: dict[str, Any] = Field(default_factory=dict)


class PageMeta(BaseModel):
    next_cursor: str | None = Field(
        description="다음 페이지 커서. null 이면 마지막 페이지다. 다음 요청의 `cursor` 에 넣는다"
    )


class Page[T](BaseModel):
    """커서 페이지네이션 목록 (CLAUDE.md §6.6)."""

    data: list[T]
    meta: PageMeta


class ErrorBody(BaseModel):
    code: str = Field(description="도메인 에러 코드. 분기는 message 가 아니라 이 값으로 한다")
    message: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_SCHEMA_REF = "#/components/schemas/ErrorResponse"


def _example(err: type[AppError]) -> dict[str, Any]:
    body = {"code": err.code, "message": err.message, "details": {}}
    return {"summary": err.message, "value": {"error": body}}


def error_fragment(errs: list[type[AppError]]) -> dict[int | str, dict[str, Any]]:
    """에러 클래스들을 OpenAPI `responses` 조각으로. 같은 상태 코드는 examples 로 합친다."""
    by_status: dict[int, list[type[AppError]]] = {}
    for err in sorted(set(errs), key=lambda e: e.code):
        by_status.setdefault(err.status, []).append(err)
    return {
        status: {
            "description": "\n".join(f"- `{e.code}` — {e.message}" for e in group),
            "content": {
                "application/json": {
                    "schema": {"$ref": ERROR_SCHEMA_REF},
                    "examples": {e.code: _example(e) for e in group},
                }
            },
        }
        for status, group in sorted(by_status.items())
    }


def raises(*errs: type[AppError]) -> dict[int | str, dict[str, Any]]:
    """`@router.get(..., responses=raises(errors.X, ...))` — 서비스가 던지는 것만 적는다."""
    return error_fragment(list(errs))
