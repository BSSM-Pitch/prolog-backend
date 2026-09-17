from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.router import router as api_router
from app.core.errors import AppError
from app.core.response import ok
from app.platform_.auth.router import router as auth_router
from app.platform_.auth.router import users_router
from app.platform_.projects.router import router as projects_router
from app.platform_.teams.router import router as teams_router

app = FastAPI(title="Prolog (StoryForge) API", version="0.1.0", root_path="")

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


@app.get("/v1/health", tags=["ops"])
async def health() -> dict[str, Any]:
    return ok({"status": "ok"})


for _router in (auth_router, users_router, teams_router, projects_router, api_router):
    app.include_router(_router, prefix="/v1")
