from decimal import Decimal

import pytest

from app.ai.narrative_review import indexed_source
from app.ai.source_arithmetic import computed_lists, quantity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,024.50", "1024.50"),
        ("一千二十一", "1021"),
        ("七百四十八", "748"),
        ("一萬五千一百四十四", "15144"),
        ("一千零四", "1004"),
        ("兩百", "200"),
        ("천이백삼십사", "1234"),
        ("천백", "1100"),
        ("백일", "101"),
        ("백이", "102"),
        ("백삼", "103"),
        ("一千二", None),
        ("12,34", None),
        ("三四百", None),
        ("一百百", None),
        ("一百零十", None),
        ("대략 백", None),
        ("一百餘", None),
    ],
)
def test_exact_quantity_or_decline_ambiguous_text(text, expected):
    assert quantity(text) == (Decimal(expected) if expected else None)


def lists(content):
    chapters, refs = indexed_source([{"chapter_id": "one", "chapter_no": 42, "content": content}])
    return computed_lists(chapters), refs


def test_same_unit_source_rows_are_summed_and_all_terms_have_original_evidence():
    # List entries cross source-passage boundaries; quantities in labels are not operands.
    content = "서고 목록\n" + "제1함" + "가" * 285 + "... 1,024.50권\n제2함: 2.25권\n제3함: 3권\n"
    result, refs = lists(content)
    assert len(result) == 1
    entry = result[0]
    assert entry["row_count"] == 3 and entry["sum"] == "1029.75"
    assert entry["unit"] == "권"
    assert len(entry["terms"][0]["source_ids"]) == 2
    for term in entry["terms"]:
        cited = "".join(refs[r]["quote"] for r in term["source_ids"])
        assert term["numeral"] in cited and term["label"] in cited


def test_chinese_lists_and_distinct_inventory_boundaries():
    content = (
        "甲 …… 七百四十八卷\n乙 …… 一千二十一卷\n丙 …… 四百卷\n\n"
        "丁 …… 一百卷\n戊 …… 二百卷\n己 …… 三百卷\n"
    )
    result, _ = lists(content)
    assert [r["sum"] for r in result] == ["2169", "600"]


@pytest.mark.parametrize(
    "bad", ["丁 …… 一千二卷", "丁 …… 未知卷", "丁 …… 一百斤", "丁: ?卷", "丁: ？卷"]
)
def test_unparsed_or_mixed_unit_row_invalidates_whole_block(bad):
    result, _ = lists("甲 …… 一百卷\n乙 …… 二百卷\n丙 …… 三百卷\n" + bad)
    assert result == []


def test_dialogue_is_not_a_numeric_list_or_part_of_an_adjacent_list():
    result, _ = lists(
        "장수가 말했다：「나는 백 명을 이겼다。」\n"
        "甲 …… 一百卷\n乙 …… 二百卷\n丙 …… 三百卷\n"
        "그는 말했다：「그것은 모두 내 물건이 아니다。」"
    )
    assert len(result) == 1 and result[0]["sum"] == "600"


@pytest.mark.parametrize("value", ["一百餘卷", "100여개", "100개가량", "100개이상"])
def test_approximate_suffix_cannot_masquerade_as_a_unit(value):
    result, _ = lists(f"甲: {value}\n乙: {value}\n丙: {value}")
    assert result == []


def test_korean_trailing_digits_are_exact_not_chinese_shorthand():
    result, _ = lists("첫째: 백일개\n둘째: 백이개\n셋째: 백삼개")
    assert len(result) == 1 and result[0]["sum"] == "306"
