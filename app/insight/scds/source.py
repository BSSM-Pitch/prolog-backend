"""Only an exact, unambiguous quotation can be used to edit a manuscript."""

import hashlib
import re


def revision(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def edit_problem(text: str, event: str, quote: str | None) -> str | None:
    if not quote or quote not in event:
        return "원문과 일치하는 인용을 확인하지 못했어요. 원고에서 직접 확인해 주세요."
    if text.count(quote) != 1:
        return (
            "인용문이 없거나 여러 곳에 있어 수정 위치를 확정할 수 없어요. 원고에서 확인해 주세요."
        )
    # Older clients collapsed paragraph whitespace. Preserve compatibility without
    # guessing a sentence or accepting a changed passage.
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()
    context = normalize(event)
    if not context or normalize(text).count(context) != 1:
        return "검사한 뒤 이 장면이 바뀌었어요. 현재 원고를 다시 검사해 주세요."
    return None
