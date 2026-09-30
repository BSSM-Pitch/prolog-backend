"""응답 계약 검사 — `uv run python -m scripts.verify.contract [e2e_responses.json]`.

앱에서 openapi 를 새로 뽑아 아래를 검사하고, 하나라도 어기면 exit 1 이다.

1. 본문이 있는 성공 응답은 전부 타입(프로퍼티)이 있다 — 빈 object 가 없다.
2. `app.core.errors` 의 도메인 에러 코드가 스펙에 전부 나온다(`UNREACHABLE` 제외).
3. 422 · `HTTPValidationError` 가 없다 — 실제 검증 실패는 400 `INVALID_INPUT` 이다.
4. 모든 오퍼레이션에 사람이 쓴 summary 와 description 이 있다(함수명 자동 생성 금지).
5. 스키마 이름 규칙 — 프론트 생성 코드의 타입 이름이 되므로 한 번 정하면 못 바꾼다.
   PascalCase(`^[A-Z][A-Za-z0-9]*$`)만 허용한다. 모듈 경로(`app__platform___X`)·제네릭
   (`Envelope_X_`)·입출력 분리(`X-Output`) 흔적이 여기서 걸린다. 끊어진 `$ref` 도 없어야 한다.
6. (인자를 주면) e2e 가 실제로 받은 응답의 필드 집합이 스펙의 필드 집합과 같다.
"""

import json
import re
import sys
from typing import Any

from fastapi.routing import APIRoute, iter_route_contexts

from app.core.errors import AppError
from app.main import app

# 스펙에 없어도 되는 코드와 그 이유. 도달 불가능한 코드를 선언하면 그게 오히려 거짓말이다.
UNREACHABLE: dict[str, str] = {}  # INVALID_STATUS_TRANSITION 은 AIQ 재시도가 낸다

SCHEMA_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def resolve(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """$ref · nullable anyOf · array 를 따라가 실제 object 스키마를 얻는다."""
    while schema:
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        elif "anyOf" in schema:
            non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
            if len(non_null) != 1:
                return schema
            schema = non_null[0]
        elif schema.get("type") == "array":
            schema = schema["items"]
        else:
            return schema
    return {}


def spec_op(spec: dict[str, Any], method: str, path: str) -> dict[str, Any] | None:
    path = path.partition("?")[0]  # 쿼리스트링은 경로 템플릿에 없다
    for template, methods in spec["paths"].items():
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), "/v1" + path):
            return methods.get(method.lower())
    return None


def fields_of(spec: dict[str, Any], schema: dict[str, Any]) -> set[str] | None:
    """union 이면 None(여러 모양)을 돌려준다."""
    resolved = resolve(spec, schema)
    if "anyOf" in resolved:
        return None
    return set(resolved.get("properties", {}))


def check_spec(spec: dict[str, Any]) -> None:
    text = json.dumps(spec)

    # 1 · 3
    ops = typed = 0
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            for status, res in op["responses"].items():
                check(status != "422", f"422 선언: {method.upper()} {path}")
                if status.startswith("2") and status != "204":
                    ops += 1
                    schema = res.get("content", {}).get("application/json", {}).get("schema")
                    if resolve(spec, schema).get("properties"):
                        typed += 1
                    else:
                        check(False, f"빈 성공 스키마: {method.upper()} {path} {status}")
    schemas = spec.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        check(name not in schemas, f"스키마 {name} 가 남아 있다")
    print(f"[1] 본문 있는 성공 응답 {ops}개 중 타입 있음 {typed}")
    print(f"[3] 422 선언 {sum(f.startswith('422 ') for f in failures)}회")

    # 2
    codes = sorted({cls.code for cls in AppError.__subclasses__()})
    shown = [c for c in codes if re.search(rf"\b{c}\b", text)]
    for c in codes:
        check(c in shown or c in UNREACHABLE, f"스펙에 없는 에러 코드: {c}")
    print(f"[2] 도메인 에러 코드 {len(codes)}개 중 스펙에 등장 {len(shown)} · 제외 {UNREACHABLE}")

    # 4
    auto = 0
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        generated = (route.name or "").replace("_", " ").title()
        label = f"{sorted(route.methods or ())} {route.path_format}"
        if not route.summary or route.summary == generated:
            auto += 1
            check(False, f"summary 자동 생성: {label}")
        check(bool(route.description and route.description.strip()), f"description 없음: {label}")
    print(f"[4] 자동 생성 summary {auto}개")

    # 5
    bad = [n for n in schemas if not SCHEMA_NAME.fullmatch(n)]
    for n in bad:
        check(False, f"스키마 이름 규칙 위반: {n}")
    dangling = set(re.findall(r'"#/components/schemas/([^"]+)"', text)) - set(schemas)
    for n in sorted(dangling):
        check(False, f"끊어진 $ref: {n}")
    print(f"[5] 스키마 {len(schemas)}개 중 이름 규칙 위반 {len(bad)} · 끊어진 $ref {len(dangling)}")


def check_e2e(spec: dict[str, Any], log_path: str) -> None:
    print("[6] e2e 실제 응답 ↔ 스펙")
    for entry in json.load(open(log_path)):
        body = entry.get("body")
        if "method" not in entry or not isinstance(body, dict) or "data" not in body:
            continue
        op = spec_op(spec, entry["method"], entry["path"]) or {}
        res = op.get("responses", {}).get(str(entry["status"]), {})
        schema = res.get("content", {}).get("application/json", {}).get("schema")
        props = resolve(spec, schema).get("properties", {})
        data = body["data"]
        item = data[0] if isinstance(data, list) and data else data
        actual = set(item) if isinstance(item, dict) else set()
        expected = fields_of(spec, props.get("data", {}))
        if expected is None:  # union — 분기 중 하나와 맞으면 된다
            branches = resolve(spec, props["data"]).get("anyOf", [])
            data_ok = any(fields_of(spec, b) == actual for b in branches)
        else:
            data_ok = expected == actual or not actual  # 빈 목록은 비교할 필드가 없다
        meta_expected = fields_of(spec, props.get("meta", {}))
        meta_actual = set(body.get("meta") or {})
        meta_ok = not meta_expected or meta_expected == meta_actual
        mark = "OK" if data_ok and meta_ok else "어긋남"
        print(f"  {mark:<4} {entry['step']:<16} {entry['status']} data {len(actual)}필드")
        check(data_ok, f"data 필드 불일치: {entry['step']} 실제={sorted(actual)} 스펙={expected}")
        check(meta_ok, f"meta 필드 불일치: {entry['step']} 실제={sorted(meta_actual)}")


def main() -> None:
    spec = app.openapi()
    check_spec(spec)
    if len(sys.argv) > 1:
        check_e2e(spec, sys.argv[1])
    if failures:
        print(f"\n실패 {len(failures)}건")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\n계약 검사 통과")


if __name__ == "__main__":
    main()
