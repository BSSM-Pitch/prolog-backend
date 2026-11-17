"""Conservative arithmetic for explicit source lists, not inferred world rules.

Only contiguous, fully parseable rows with the same literal unit are summed.
Whether the list describes a total, a subset, or a different owner/time remains
a semantic question for the reviewer. No manuscript text is removed or replaced.
"""

import re
from decimal import Decimal
from typing import Any

_DIGITS = dict(zip("零〇一二三四五六七八九", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9], strict=True))
_SMALL = {"十": 10, "百": 100, "千": 1000}
_LARGE = {"萬": 10000, "億": 100000000}
_NORMAL = str.maketrans(
    "两兩万亿壹貳贰參叁肆伍陸陆柒捌玖拾佰仟", "二二萬億一二二三三四五六六七八九十百千"
)
_SINO_KOREAN = str.maketrans("영공일이삼사오육칠팔구십백천만억", "零零一二三四五六七八九十百千萬億")
_NUMERALS = "零〇一二三四五六七八九十百千萬億两兩万亿壹貳贰參叁肆伍陸陆柒捌玖拾佰仟"
_KOREAN = "영공일이삼사오육칠팔구십백천만억"
_LIST_LINE = re.compile(
    r"(?:\.{2,}|…{2,}|[\t|])|^[^:：。！？!?.]{1,80}[:：]\s*[^:：。「」『』]{1,40}$"
)
_APPROXIMATION = re.compile(r"^[餘余多來来幾几여]|(?:이상|이하|정도|가량|남짓|쯤)$")
_ROW = re.compile(
    r"(?P<label>.+?)(?:\s*(?:\.{2,}|…{2,}|[:：\t|])\s*)"
    rf"(?P<number>[0-9][0-9,.]*|[{_NUMERALS}]+|[{_KOREAN}]+)"
    r"\s*(?P<unit>[^\s\d.,:：|…]{1,12})\s*\|?\s*$"
)


def quantity(text: str) -> Decimal | None:
    """Parse explicit positional numerals; reject shorthand such as 一千二."""
    if re.fullmatch(r"(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?", text):
        return Decimal(text.replace(",", ""))
    korean = bool(re.fullmatch(rf"[{_KOREAN}]+", text))
    if korean:
        text = text.translate(_SINO_KOREAN)
    text = text.translate(_NORMAL)
    if not text or any(c not in _DIGITS | _SMALL | _LARGE for c in text):
        return None
    if all(c in _DIGITS for c in text):
        return Decimal("".join(str(_DIGITS[c]) for c in text))
    total = section = number = 0
    last_small = 10000
    last_large = 10**12
    digit_pending = False
    last_zero = False
    for i, char in enumerate(text):
        if char in _DIGITS:
            value = _DIGITS[char]
            if digit_pending and number != 0:
                return None
            # In conventional prose, trailing 'two' after 'a thousand' is ambiguous.
            if not korean and i == len(text) - 1 and value and last_small >= 100 and not last_zero:
                return None
            number = value
            digit_pending = True
            last_zero = value == 0
        elif char in _SMALL:
            unit = _SMALL[char]
            if unit >= last_small:
                return None
            # Omitted leading one is ordinary in 十 / 십백천, not arbitrary mixed forms.
            if digit_pending and number == 0:
                return None
            if not korean and not digit_pending and section and unit != 10:
                return None
            section += (number if digit_pending else 1) * unit
            number = 0
            digit_pending = False
            last_small = unit
            last_zero = False
        else:
            unit = _LARGE[char]
            if unit >= last_large or section + number == 0:
                return None
            total += (section + number) * unit
            section = number = 0
            digit_pending = False
            last_small = 10000
            last_large = unit
            last_zero = False
    return Decimal(total + section + number)


def computed_lists(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for chapter in chapters:
        passages = chapter["passages"]
        content = "".join(p["text"] for p in passages)
        bounds = []
        offset = 0
        for p in passages:
            bounds.append((offset, offset + len(p["text"]), p["source_id"]))
            offset += len(p["text"])
        block: list[tuple[str, int]] = []

        def flush(
            block: list[tuple[str, int]], bounds: list[tuple[int, int, str]], chapter_no: int
        ) -> None:
            terms: list[dict[str, Any]] = []
            for line, start in block:
                match = _ROW.fullmatch(line.strip())
                if (
                    not match
                    or _APPROXIMATION.search(match["unit"])
                    or (value := quantity(match["number"])) is None
                ):
                    return
                terms.append(
                    {
                        "label": match["label"].strip(),
                        "numeral": match["number"],
                        "value": str(value),
                        "unit": match["unit"],
                        "source_ids": [
                            ref for lo, hi, ref in bounds if lo < start + len(line) and hi > start
                        ],
                    }
                )
            if len(terms) < 3 or len({t["unit"] for t in terms}) != 1:
                return
            result.append(
                {
                    "chapter_no": chapter_no,
                    "operation": "sum",
                    "unit": terms[0]["unit"],
                    "row_count": len(terms),
                    "terms": terms,
                    "sum": str(sum((Decimal(t["value"]) for t in terms), Decimal(0))),
                }
            )

        offset = 0
        for raw in content.splitlines(keepends=True):
            line = raw.strip()
            if not line:
                flush(block, bounds, chapter["chapter_no"])
                block = []
            elif _LIST_LINE.search(line):
                block.append((raw.rstrip("\r\n"), offset))
            else:
                flush(block, bounds, chapter["chapter_no"])
                block = []
            offset += len(raw)
        flush(block, bounds, chapter["chapter_no"])
    return result
