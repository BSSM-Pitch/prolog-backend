"""Request-local higher reasoning must not change other jobs' configuration."""

from unittest.mock import MagicMock, patch

from prolog_ai.core.llm import LLMConfig
from prolog_ai.modules.aiq.schema import AIQOutput

from app.ai.reasoning import call_llm


def test_glm_qa_requests_high_effort_without_mutating_default(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    config = LLMConfig(
        "nvidia",
        "https://example.invalid/v1",
        "z-ai/glm-5.3-flash",
        "NVIDIA_API_KEY",
        0.5,
        1.0,
        "low",
    )
    client = MagicMock()
    with (
        patch("app.ai.reasoning.get_llm_config", return_value=config),
        patch("openai.OpenAI", return_value=client),
        patch("app.ai.reasoning._request_with_client", return_value={"content": "완성"}) as request,
    ):
        result = call_llm("질문", schema=AIQOutput, timeout=120, max_retries=1)
    assert result == {"content": "완성"}
    assert request.call_args.args[1].reasoning_effort == "high"
    assert config.reasoning_effort == "low"


def test_other_models_keep_their_existing_transport() -> None:
    config = LLMConfig(
        "openrouter", "https://example.invalid/v1", "other", "OPENROUTER_API_KEY", 0, 1
    )
    with (
        patch("app.ai.reasoning.get_llm_config", return_value=config),
        patch("app.ai.reasoning.default_call_llm", return_value={"content": "완성"}) as default,
    ):
        assert call_llm("질문", schema=AIQOutput, timeout=120, max_retries=1) == {"content": "완성"}
    default.assert_called_once()
