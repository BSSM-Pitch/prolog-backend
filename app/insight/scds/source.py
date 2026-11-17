"""Only an exact, unambiguous quotation can be used to edit a manuscript."""

import hashlib


def revision(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def edit_problem(text: str, event: str, quote: str | None) -> str | None:
    if not quote or quote not in event:
        return "원문과 일치하는 인용을 확인하지 못했어요. 원고에서 직접 확인해 주세요."
    if text.count(quote) != 1:
        return (
            "인용문이 없거나 여러 곳에 있어 수정 위치를 확정할 수 없어요. 원고에서 확인해 주세요."
        )
    # The quote itself must still match. Edits elsewhere do not invalidate it;
    # the caller checks the whole chapter revision at the actual save boundary.
    return None
