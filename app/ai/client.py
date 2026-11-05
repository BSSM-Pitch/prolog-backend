"""AI 포트 — `prolog-ai` 패키지를 감싼다. Google OAuth · Storage · Extractor 와 같은 패턴이다.

패키지 함수는 **동기**이고 **예외를 던지지 않는다**(`@public_api`): 성공은 `{"data", "meta"}`,
실패는 `{"error": {"code", "message", "details"}}` 다. 워커는 스레드에서 부른다.

- 테스트는 `FakeAI`(tests/conftest.py)를 쓴다. 패키지의 `USE_FAKE_LLM=1` 은 스키마의 필수 필드만
  채운 빈 응답뿐이라 시나리오(추출 결과 · 시간 초과 · 실패)를 줄 수 없다.
- 요청 경로에서 부르는 곳(SCDS 룰 검출 등)은 `Depends(ai_client)` 로 받고, 테스트는
  `dependency_overrides` 로 갈아끼운다. 워커는 핸들러 인자로 받는다.
- 패키지는 `.env` 를 읽지 않고 환경변수만 본다. 앱 Settings 의 값을 첫 사용 때 환경변수로 넘긴다.
- **usage(토큰 수)는 주지 않는다** — 패키지가 응답의 도구 인자만 꺼낸다. 비용 컬럼은 만들지 않았다.
"""

import os
from typing import Any, Protocol

from app.core.config import settings

Result = dict[str, Any]


class AIClient(Protocol):
    def run_rule_duplicates(
        self, candidates: list[dict[str, Any]], rules: list[dict[str, Any]]
    ) -> Result: ...

    def run_rule_review(
        self, event: dict[str, Any], rules: list[dict[str, Any]], characters: list[dict[str, Any]]
    ) -> Result: ...

    def run_character_discovery(self, payload: dict[str, Any]) -> Result: ...

    def run_character_assessment(self, payload: dict[str, Any]) -> Result: ...

    def run_nlcd(self, source_text: str) -> Result: ...

    def run_rex(self, manuscript_text: str) -> Result: ...

    def run_aiq(
        self,
        question: str,
        manuscript_text: str,
        scope: str = "project",
        selection_range: dict[str, int] | None = None,
        messages: list[dict[str, Any]] | None = None,
        *,
        world_rules: list[dict[str, Any]] | None = None,
    ) -> Result: ...

    def run_scds_rules(
        self,
        event: dict[str, Any],
        world_rules: list[dict[str, Any]],
        characters: list[dict[str, Any]] | None = None,
    ) -> Result: ...

    def run_scds_analysis(
        self,
        event: dict[str, Any],
        rule_result: dict[str, Any],
        characters: list[dict[str, Any]] | None = None,
    ) -> Result: ...

    def run_ssm(
        self, manuscript_text: str, characters: list[dict[str, Any]] | None = None
    ) -> Result: ...

    def run_character_memory(
        self,
        project_id: str,
        scenes: list[dict[str, Any]],
        characters: list[dict[str, Any]],
        previous_memory: dict[str, Any] | None = None,
    ) -> Result: ...


def is_retryable(error: dict[str, Any]) -> bool:
    """패키지 에러를 잡 자동 재시도 대상(인프라 실패)으로 볼 것인가.

    - `AI_*_TIMEOUT`: 재시도한다. 공급자 지연은 일시적이다.
    - `AI_*_FAILED`: 재시도하지 않는다. 패키지가 **이미** 연결 오류 · 408 · 409 · 429 · 5xx 를
      두 번 다시 시도한 뒤의 실패라, 남은 원인은 인증 오류(4xx) · 출력 잘림 · 스키마 불일치
      (temperature 0 이라 같은 입력에 같은 출력) · 예상 못 한 예외다 — 다시 해도 대개 같고
      비용만 든다. 사용자가 `/retry` 한다.
    - `INVALID_INPUT` 등 나머지: 도메인 실패. 같은 입력이면 같은 결과다.
    """
    return str(error.get("code", "")).endswith("_TIMEOUT")


class PrologAI:
    """실제 구현. 패키지는 첫 호출 때 import 한다(테스트·API 프로세스가 openai 를 끌어오지 않게)."""

    def __init__(self) -> None:
        for key, value in {
            "NVIDIA_API_KEY": settings.nvidia_api_key,
            "PROLOG_AI_PROVIDER": settings.prolog_ai_provider,
            "PROLOG_AI_REASONING_EFFORT": settings.prolog_ai_reasoning_effort,
        }.items():
            if value:
                os.environ.setdefault(key, value)
        if settings.openrouter_api_key:
            os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)
        if settings.prolog_ai_model:
            os.environ.setdefault("PROLOG_AI_MODEL", settings.prolog_ai_model)

    def run_rule_duplicates(
        self, candidates: list[dict[str, Any]], rules: list[dict[str, Any]]
    ) -> Result:
        from app.ai.rule_review import duplicates

        return duplicates(candidates, rules)

    def run_rule_review(
        self, event: dict[str, Any], rules: list[dict[str, Any]], characters: list[dict[str, Any]]
    ) -> Result:
        from app.ai.rule_review import review

        return review(event, rules, characters)

    def run_character_discovery(self, payload: dict[str, Any]) -> Result:
        from app.ai.character_discovery import run

        return run(payload)

    def run_nlcd(self, source_text: str) -> Result:
        from prolog_ai import run_nlcd

        return run_nlcd(source_text)

    def run_character_assessment(self, payload: dict[str, Any]) -> Result:
        from app.ai.character_assessment import run

        return run(payload)

    def run_character_memory(
        self,
        project_id: str,
        scenes: list[dict[str, Any]],
        characters: list[dict[str, Any]],
        previous_memory: dict[str, Any] | None = None,
    ) -> Result:
        from prolog_ai import run_character_memory

        return run_character_memory(
            project_id,
            scenes,
            characters,
            previous_memory=previous_memory,
            chunk_chars=3000,
            batch_chars=12000,
            max_llm_calls=10,
        )

    def run_rex(self, manuscript_text: str) -> Result:
        from prolog_ai import run_rex

        return run_rex(manuscript_text)

    def run_aiq(
        self,
        question: str,
        manuscript_text: str,
        scope: str = "project",
        selection_range: dict[str, int] | None = None,
        messages: list[dict[str, Any]] | None = None,
        *,
        world_rules: list[dict[str, Any]] | None = None,
    ) -> Result:
        from app.ai.grounded_qa import answer

        return answer(
            question, manuscript_text, scope, selection_range, messages, world_rules or []
        )

    def run_scds_rules(
        self,
        event: dict[str, Any],
        world_rules: list[dict[str, Any]],
        characters: list[dict[str, Any]] | None = None,
    ) -> Result:
        from prolog_ai import run_scds_rules

        return run_scds_rules(event, world_rules, characters)

    def run_scds_analysis(
        self,
        event: dict[str, Any],
        rule_result: dict[str, Any],
        characters: list[dict[str, Any]] | None = None,
    ) -> Result:
        from prolog_ai import run_scds_analysis

        return run_scds_analysis(event, rule_result, characters)

    def run_ssm(
        self, manuscript_text: str, characters: list[dict[str, Any]] | None = None
    ) -> Result:
        from prolog_ai import run_ssm

        return run_ssm(manuscript_text, characters)


def ai_client() -> AIClient:
    """FastAPI 의존성. 테스트는 dependency_overrides 로 갈아끼운다."""
    return PrologAI()
