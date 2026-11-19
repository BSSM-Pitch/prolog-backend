"""Request-scoped QA previews; never persist an unchecked draft as the final answer."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from pydantic_core import from_json

listener: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "qa_progress", default=None
)


def emit(stage: str, content: str = "") -> None:
    callback = listener.get()
    if callback:
        callback({"stage": stage, "content": content})


def review_arguments(arguments: str) -> None:
    if not arguments:
        emit("reviewing")
        return
    try:
        data = from_json(arguments, allow_partial="trailing-strings")
    except ValueError:
        return
    # Scope must be decided before any preview; only the independent review is streamed.
    if (
        isinstance(data, dict)
        and data.get("scope_allowed") is True
        and isinstance(data.get("content"), str)
    ):
        emit("writing", data["content"])
