from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.api.nlcd import router as nlcd_forward_router
from app.api.router import router as api_router
from app.authoring.ass.router import router as ass_router
from app.authoring.nlcd.router import router as nlcd_router
from app.authoring.rex.router import router as rex_router
from app.content.manuscripts.router import router as manuscripts_router
from app.core import openapi
from app.core.errors import AppError
from app.core.response import Envelope, ok
from app.insight.aiq.router import router as aiq_router
from app.insight.fts.router import router as fts_router
from app.insight.scds.router import router as scds_router
from app.platform_.auth.router import router as auth_router
from app.platform_.auth.router import users_router
from app.platform_.notifications.router import router as notifications_router
from app.platform_.notifications.router import settings_router as notification_settings_router
from app.platform_.projects.router import router as projects_router
from app.platform_.teams.router import router as teams_router

app = FastAPI(
    title="Prolog (StoryForge) API",
    version="0.1.0",
    root_path="",
    description=(
        "성공 응답은 `{data, meta}`, 실패는 `{error: {code, message, details}}` 다. "
        "분기는 HTTP 상태가 아니라 `error.code` 로 한다 — 같은 상태 코드에 코드가 여럿이다.\n\n"
        "입력 검증 실패는 전부 **400 `INVALID_INPUT`** 이며 `details.fields` 에 위치가 온다. "
        "인증은 `Authorization: Bearer <access_token>`. "
        "목록은 커서 페이지네이션이다: `meta.next_cursor` 를 다음 요청의 `cursor` 로 넘기고, "
        "null 이면 끝이다."
    ),
)

_HTTP_CODES = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 명세 공통 코드는 INVALID_INPUT(400) 이다. VALIDATION_ERROR·422 는 명세에 없다.
    return _error(
        400,
        "INVALID_INPUT",
        "요청 값이 올바르지 않습니다",
        {"fields": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]},
    )


@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    return _error(exc.status_code, code, str(exc.detail))


class Health(BaseModel):
    status: str


@app.get("/v1/health", tags=["ops"], summary="헬스체크", response_model=Envelope[Health])
async def health() -> dict[str, Any]:
    """앱 프로세스가 떠 있으면 `ok`. DB·큐 상태는 보지 않는다."""
    return ok({"status": "ok"})


for _router in (
    auth_router,
    users_router,
    teams_router,
    projects_router,
    notifications_router,
    notification_settings_router,
    manuscripts_router,
    ass_router,
    rex_router,
    fts_router,
    aiq_router,
    scds_router,
    api_router,
    nlcd_router,
    nlcd_forward_router,
):
    app.include_router(_router, prefix="/v1")

openapi.install(app)
