import asyncio
from collections import Counter
from types import SimpleNamespace

import pytest
from prolog_ai.memory import compact

from app.insight.character_memory import scale


def corpus():
    rows = []
    for subject, count in enumerate([6, 50, 100, 244]):
        for _ in range(count):
            i = len(rows)
            rows.append(
                {
                    "i": i,
                    "c": subject,
                    "order": i + 1,
                    "k": "action",
                    "p": "depicted",
                    "b": "choice",
                    "quote": f"근거 {i}",
                    "context": "전체 문맥",
                }
            )
    return {"characters": [{"i": i, "name": str(i)} for i in range(4)], "observations": rows}


def test_subjects_are_covered_once_and_smallest_real_task_primes_cache():
    p = corpus()
    groups = scale.proposal_groups(p)
    assert groups[0] == [0]
    assert Counter(c for group in groups for c in group) == Counter(range(4))
    assert scale.proposal_groups({**p, "observations": p["observations"][:399]}) == []


@pytest.mark.parametrize("per_subject", [1, 20])
async def test_parallel_output_keeps_full_corpus_and_every_late_counterexample(
    monkeypatch, per_subject
):
    p = corpus()
    monkeypatch.setenv("PROLOG_AI_PROVIDER", "openai")
    monkeypatch.setenv("PROLOG_AI_MODEL", "gpt-6.1-sol")
    monkeypatch.setattr(compact, "synthesis_payload", lambda memory: p)
    applied = []
    monkeypatch.setattr(
        compact,
        "apply_patterns",
        lambda memory, proposals, assessments: applied.append((proposals, assessments)),
    )
    primed = False
    active = peak = 0
    subjects_seen = []

    async def execute(stage, payload, label):
        nonlocal primed, active, peak
        assert payload["observations"] == p["observations"]
        assert payload["characters"] == p["characters"]
        if stage == "propose":
            subjects = payload["requested_subjects"]
            if subjects != [0]:
                assert primed
            subjects_seen.extend(subjects)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            result = {"proposals": []}
            for c in subjects:
                ids = [r["i"] for r in p["observations"] if r["c"] == c]
                result["proposals"].extend(
                    {
                        "c": c,
                        "description": f"후보 {i}",
                        "condition": "위기",
                        "seeds": [ids[0]],
                        "hypothesis": None,
                        "alternatives": [],
                    }
                    for i in range(per_subject)
                )
            primed = True
        else:
            result = {"assessments": []}
            for i, proposal in enumerate(payload["proposals"]):
                ids = [r["i"] for r in p["observations"] if r["c"] == proposal["c"]]
                result["assessments"].append({"i": i, "support": ids[:-1], "counter": ids[-1:]})
        return compact.check(stage, payload, result)

    await scale.synthesize(SimpleNamespace(), execute)
    assert peak == 3
    assert Counter(subjects_seen) == Counter(range(4))
    assert sum(len(proposals["proposals"]) for proposals, _ in applied) == 4 * per_subject
    for proposals, assessments in applied:
        for decision in assessments["assessments"]:
            subject = proposals["proposals"][decision["i"]]["c"]
            last = max(r["i"] for r in p["observations"] if r["c"] == subject)
            assert decision["counter"] == [last]
