"""OpenAPI 스펙을 실제 동작에 맞춘다.

FastAPI 기본 스펙은 두 군데서 거짓말을 한다.

1. 검증 실패를 **422** `HTTPValidationError` 로 적는다. 실제로는 `app.main` 의 핸들러가
   400 `INVALID_INPUT` 으로 바꿔 내보낸다. 422 는 한 번도 나가지 않는다.
2. 의존성이 던지는 에러(인증 401 · 권한 403 · 없는 팀/프로젝트 404)는 어디에도 없다.

그래서 기본 스펙을 뽑은 뒤 422 를 지우고, 라우트의 의존성 그래프를 따라가며
`deps.DEPENDENCY_RAISES` 에 등록된 에러와 입력 검증(`INVALID_INPUT`)을 붙인다.
라우트가 `responses=raises(...)` 로 직접 적은 에러와 같은 상태 코드면 하나로 합친다.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from app.core.deps import DEPENDENCY_RAISES
from app.core.errors import AppError, InvalidInput
from app.core.response import ErrorResponse, error_fragment

_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _all_errors() -> dict[str, type[AppError]]:
    return {cls.code: cls for cls in AppError.__subclasses__()}


def _walk(dependant: Dependant) -> tuple[list[type[AppError]], bool]:
    """(의존성이 던지는 에러, 검증할 입력이 하나라도 있는가). 내부 API 없이 트리를 직접 돈다."""
    found: list[type[AppError]] = []
    has_input = bool(
        dependant.path_params
        or dependant.query_params
        or dependant.header_params
        or dependant.cookie_params
        or dependant.body_params
    )
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.extend(DEPENDENCY_RAISES.get(sub.call, ()))
        sub_found, sub_input = _walk(sub)
        found.extend(sub_found)
        has_input = has_input or sub_input
    return found, has_input


def derived_errors(route: RouteContext) -> list[type[AppError]]:
    """라우트가 선언하지 않아도 의존성·파라미터 때문에 반드시 날 수 있는 에러."""
    found, has_input = _walk(route.dependant)
    return [*found, InvalidInput] if has_input else found


def _declared_codes(responses: dict[str, Any]) -> list[str]:
    return [
        code
        for status, response in responses.items()
        if status[0] in "45"
        for code in response.get("content", {}).get("application/json", {}).get("examples", {})
    ]


def build(app: FastAPI) -> dict[str, Any]:
    spec = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    known = _all_errors()
    # include_router 는 라우트를 펼치지 않고 감싼다(FastAPI 0.14x). prefix 가 붙은 실효 라우트는
    # get_openapi 와 같은 방식(iter_route_contexts)으로 얻는다.
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        for method in route.methods or ():
            op = spec["paths"][route.path_format][method.lower()]
            responses: dict[str, Any] = op["responses"]
            responses.pop("422", None)
            errs = [known[c] for c in _declared_codes(responses)] + derived_errors(route)
            for status in [s for s in responses if s[0] in "45"]:
                del responses[status]
            responses.update({str(k): v for k, v in error_fragment(errs).items()})

    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for name in _FASTAPI_VALIDATION_SCHEMAS:
        schemas.pop(name, None)
    error_schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(error_schema.pop("$defs", {}))
    schemas["ErrorResponse"] = error_schema
    return spec


def install(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
