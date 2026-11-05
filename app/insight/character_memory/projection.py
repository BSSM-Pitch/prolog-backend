"""저장된 기억만 투영한다. 새 해석이나 확정 설정을 생성하지 않는다."""

from collections import Counter
from typing import Any

from prolog_ai.memory.source import validate_memory


def situation_metrics(
    records: list[dict[str, Any]], scene_orders: list[int], character_id: str, window: int = 3
) -> dict[str, Any]:
    """빈도는 추출 기록 기준이다. 선택 비중은 성격/정신 건강 점수가 아니다."""
    orders = sorted(scene_orders)

    def period(selected: list[int]) -> dict[str, Any]:
        rows = [r for r in records if r["order"] in selected]
        # 겹치는 추출 청크가 정확히 같은 원문/역할을 두 번 남겨도 중복 집계하지 않는다.
        unique: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        for r in rows:
            e = r["evidence"]
            unique.setdefault((e["scene_id"], e["start"], e["end"], r["behavior_role"]), r)
        rows = list(unique.values())
        choices = [r["observation_id"] for r in rows if r["behavior_role"] == "choice"]
        reactions = [
            r["observation_id"] for r in rows if r["behavior_role"] == "involuntary_reaction"
        ]
        classified = choices + reactions
        partner_counts: Counter[str] = Counter()
        interaction_ids = []
        for r in rows:
            partners = set(r["interlocutor_ids"]) - {character_id}
            partner_counts.update(partners)
            if partners:
                interaction_ids.append(r["observation_id"])
        return {
            "orders": selected,
            "record_count": len(rows),
            "record_ids": [r["observation_id"] for r in rows],
            "choice_count": len(choices),
            "reaction_count": len(reactions),
            "choice_ids": choices,
            "reaction_ids": reactions,
            "choice_share": round(100 * len(choices) / len(classified), 1) if classified else None,
            "classified_count": len(classified),
            "other_count": len(rows) - len(classified),
            "small_sample": len(classified) < 5,
            "partner_counts": dict(partner_counts),
            "interaction_ids": interaction_ids,
        }

    recent = period(orders[-window:])
    previous = period(orders[-2 * window : -window])
    comparable = (
        len(recent["orders"]) == len(previous["orders"]) == window
        and recent["choice_share"] is not None
        and previous["choice_share"] is not None
    )
    return {
        "basis": "extracted_records",
        "window_size": window,
        "recent": recent,
        "previous": previous,
        "choice_delta_pp": round(recent["choice_share"] - previous["choice_share"], 1)
        if comparable
        else None,
        "timeline": [{"order": order, **period([order])} for order in orders],
    }


def dossier(
    value: dict[str, Any], character_id: str, titles: dict[str, str], through_order: int | None
) -> dict[str, Any]:
    memory = validate_memory(value, require_complete=True)
    people = {c.character_id: c for c in memory.characters}
    scenes = {
        s.scene_id: s for s in memory.scenes if through_order is None or s.order <= through_order
    }
    checks = {v.observation_id: v for v in memory.speaker_verifications}
    observations = {
        o.observation_id: o for o in memory.observations if o.evidence.scene_id in scenes
    }

    def record(oid: str) -> dict[str, Any]:
        o = observations[oid]
        e = o.evidence
        scene = scenes[e.scene_id]
        check = checks.get(oid)
        return {
            **o.model_dump(),
            "order": scene.order,
            "chapter_id": scene.chapter_id,
            "scene_title": titles.get(scene.scene_id) or f"장면 {scene.order}",
            "story_time": scene.story_time,
            "interlocutors": [people[i].name for i in o.interlocutor_ids],
            "source": {
                "before": scene.text[max(0, e.start - 240) : e.start],
                "quote": scene.text[e.start : e.end],
                "after": scene.text[e.end : e.end + 240],
            },
            "speaker_check": None
            if check is None
            else {
                "reason": check.reason,
                "adjudicated": check.adjudicated,
                "evidence": check.evidence.model_dump(),
            },
        }

    def ordered(ids: list[str]) -> list[dict[str, Any]]:
        return [
            record(oid)
            for oid in sorted(
                ids,
                key=lambda i: (
                    scenes[observations[i].evidence.scene_id].order,
                    observations[i].evidence.start,
                    i,
                ),
            )
        ]

    certain = ordered(
        [
            o.observation_id
            for o in observations.values()
            if o.character_id == character_id and o.attribution == "certain"
        ]
    )
    uncertain_ids = []
    for o in observations.values():
        if o.attribution != "uncertain":
            continue
        check = checks.get(o.observation_id)
        candidates = {o.character_id}
        if check:
            candidates.update([check.original_character_id, check.proposed_character_id])
        if character_id in candidates:
            uncertain_ids.append(o.observation_id)
    patterns = [
        p
        for p in memory.patterns
        if p.character_id == character_id
        and (through_order is None or p.analyzed_through_order <= through_order)
    ]
    patterns.sort(key=lambda p: (p.status != "contested", -p.supporting_scene_count, p.pattern_id))
    pattern_ids = {p.pattern_id for p in patterns}
    return {
        "character_id": character_id,
        "character_name": people[character_id].name if character_id in people else None,
        "snapshot_id": memory.snapshot_id,
        "mode": memory.mode,
        "through_order": through_order,
        "counts": {
            "observations": len(certain),
            "patterns": len(patterns),
            "uncertain": len(uncertain_ids),
            "scenes": len({o["evidence"]["scene_id"] for o in certain}),
            **{
                kind: sum(o["kind"] == kind for o in certain)
                for kind in ("speech", "action", "inner_thought", "narration")
            },
        },
        "observations": certain,
        "metrics": situation_metrics(certain, [s.order for s in scenes.values()], character_id),
        "uncertain_observations": ordered(uncertain_ids),
        # Refer to observations once; hundreds of patterns can share the same long quotes.
        "patterns": [p.model_dump() for p in patterns],
        "hypotheses": [h.model_dump() for h in memory.hypotheses if h.pattern_id in pattern_ids],
        "coverage": {
            "exhaustive": False,
            "patterns_hidden_by_cutoff": sum(
                p.character_id == character_id and p.pattern_id not in pattern_ids
                for p in memory.patterns
            ),
            "speaker_verification_complete": memory.speaker_verification_version == "v1",
        },
    }


def character_counts(value: dict[str, Any]) -> dict[str, int]:
    return dict(
        Counter(
            o["character_id"]
            for o in value.get("observations", [])
            if o["character_id"] is not None and o["attribution"] == "certain"
        )
    )
