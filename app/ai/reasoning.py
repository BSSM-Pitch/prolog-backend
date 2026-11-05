"""Request-local GLM reasoning effort; never mutate shared process environment."""

from dataclasses import replace
from typing import Any

from prolog_ai.core.llm import (
    _request_with_client,
    get_llm_config,
)
from prolog_ai.core.llm import (
    call_llm as default_call_llm,
)
from pydantic import BaseModel


def call_llm(
    prompt: str, *, schema: type[BaseModel], timeout: float, max_retries: int
) -> dict[str, Any]:
    # The pinned AI package exposes effort in LLMConfig, but not in call_llm.
    # Isolate its transport adapter here until a public per-call option is available.
    config = get_llm_config()
    if config.provider != "nvidia" or config.reasoning_effort is None:
        return default_call_llm(prompt, schema=schema, timeout=timeout, max_retries=max_retries)
    import openai

    local_config = replace(config, reasoning_effort="high")
    with openai.OpenAI(
        api_key=local_config.require_api_key(),
        base_url=local_config.base_url,
        timeout=timeout,
        max_retries=0,
    ) as client:
        result: dict[str, Any] = _request_with_client(
            client, local_config, prompt, schema, timeout, max_retries
        )
        return result
