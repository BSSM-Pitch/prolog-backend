"""A keyword occurrence is never sufficient for a semantic verdict."""

from unittest.mock import patch

from app.ai.rule_review import review

RULES = [
    {
        "rule_id": "r1",
        "title": "미래 로그",
        "description": "정확히 7시간 뒤의 메시지만 수신",
        "violation_keywords": ["7시간"],
    }
]
TEXT = "현재 시각은 새벽 두 시였다. 발신 시각은 오늘 오전 아홉 시였다."


def assessment(verdict: str, evidence: str | None = TEXT) -> dict:
    return {
        "assessments": [
            {
                "rule_id": "r1",
                "verdict": verdict,
                "evidence": evidence,
                "reason": "모델이 시간 조건을 비교한 결과",
                "severity": None,
            }
        ]
    }


def test_model_can_reject_keyword_false_positive_after_review() -> None:
    with patch(
        "app.ai.rule_review.call_llm", side_effect=[assessment("violates"), assessment("supports")]
    ) as model:
        result = review({"content": TEXT}, RULES, [])
    assert result["data"]["assessments"][0]["verdict"] == "supports"
    assert model.call_count == 2
    assert "키워드는 검색 단서" in model.call_args_list[0].args[0]
    assert "정확히 7시간" in model.call_args_list[1].args[0]


def test_nonliteral_evidence_cannot_prove_a_violation() -> None:
    hallucinated = assessment("violates", "원고에 없는 다음 날 시각")
    with patch("app.ai.rule_review.call_llm", side_effect=[hallucinated, hallucinated]):
        result = review({"content": TEXT}, RULES, [])
    assert result["data"]["assessments"][0]["verdict"] == "undetermined"
    assert result["data"]["assessments"][0]["evidence"] is None


def test_missing_rule_verdict_fails_instead_of_saying_no_conflicts() -> None:
    with patch("app.ai.rule_review.call_llm", return_value={"assessments": []}):
        result = review({"content": TEXT}, RULES, [])
    assert result["error"]["code"] == "AI_ANALYSIS_FAILED"
