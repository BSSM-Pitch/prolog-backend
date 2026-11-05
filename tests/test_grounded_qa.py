"""Contract tests for source grounding and independent model review."""

import json
from unittest.mock import patch

import pytest
from prolog_ai.core.llm import LLMTimeoutError

from app.ai.grounded_qa import AnswerDraft, answer

RULES = [
    {"rule_id": "rule-7", "title": "미래 로그", "description": "정확히 7시간 뒤의 메시지만 수신"}
]
TEXT = "현재 시각은 새벽 두 시였다. 서윤이 읽은 로그의 발신 시각은 내일 오전 아홉 시였다."
QUESTION = (
    "세계관의 미래 로그 규칙과 이 장의 발신 시각이 서로 맞는지 확인해 줘. "
    "시간 차이를 계산해 설명해 줘."
)
DRAFT = {
    "source_facts": ["새벽 두 시", "내일 오전 아홉 시"],
    "applicable_rules": ["rule-7"],
    "calculation_summary": "7시간",
    "uncertainty": "없음",
    "content": "잘못된 초안: 7시간",
}


def test_review_receives_original_sources_and_can_correct_bad_arithmetic() -> None:
    with patch(
        "app.ai.grounded_qa.call_llm",
        side_effect=[DRAFT, {"content": "31시간이므로 7시간 규칙과 충돌합니다."}],
    ) as model:
        result = answer(QUESTION, TEXT, "project", None, [], RULES)
    assert result["data"]["content"].startswith("31시간")
    assert result["meta"]["review_passes"] == 2
    assert model.call_args_list[0].kwargs["schema"] is AnswerDraft
    for call in model.call_args_list:
        assert TEXT in call.args[0]
        assert json.dumps(RULES, ensure_ascii=False)[1:-1] in call.args[0]
    assert "잘못된 초안" in model.call_args_list[1].args[0]
    assert "독립적으로" in model.call_args_list[1].args[0]


@pytest.mark.parametrize("failure", [LLMTimeoutError("timeout"), ValueError("bad response")])
def test_failed_review_does_not_publish_unchecked_draft(failure: Exception) -> None:
    with patch("app.ai.grounded_qa.call_llm", side_effect=[DRAFT, failure]):
        result = answer(QUESTION, TEXT, "project", None, [], RULES)
    assert "error" in result
    assert "data" not in result


def test_selection_and_history_preserved_without_mutating_manuscript() -> None:
    history = [
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
    ]
    with patch(
        "app.ai.grounded_qa.call_llm", side_effect=[DRAFT, {"content": "완성 답변"}]
    ) as model:
        answer(QUESTION, TEXT, "selection", {"start": 0, "end": 12}, history, RULES)
    prompt = model.call_args_list[0].args[0]
    assert json.dumps(TEXT[:12], ensure_ascii=False) in prompt
    assert "이전 답변" in prompt
    assert '"manuscript": ' + json.dumps(TEXT, ensure_ascii=False) in prompt


def test_invalid_selection_does_not_call_model() -> None:
    with patch("app.ai.grounded_qa.call_llm") as model:
        result = answer(QUESTION, TEXT, "selection", {"start": 0, "end": 10000}, [], RULES)
    assert result["error"]["code"] == "INVALID_SELECTION_RANGE"
    model.assert_not_called()
