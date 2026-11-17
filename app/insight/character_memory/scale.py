"""Bounded long-book planning and complete cross-window pattern verification.

Every observation is retained. A proposed trait is checked against every window,
including late counterexamples, before it is materialized as a book-wide pattern.
"""

import json
from collections import defaultdict
from typing import Any

from prolog_ai.memory import compact
from prolog_ai.memory.schema import Memory

MAX_BATCHES = 256
SYNTHESIS_BYTES = 220_000
REPAIR_BATCHES = 4


def request_limit(batch_count: int) -> int:
    # Extraction, verification, ownership adjudication, repairs and synthesis.
    # Physical reservations survive retries. Small books retain the original cap.
    return (
        max(compact.MAX_REQUESTS, batch_count * 8 + 32) if batch_count > 9 else compact.MAX_REQUESTS
    )


def windows(payload: dict[str, Any], byte_limit: int = SYNTHESIS_BYTES) -> list[dict[str, Any]]:
    """Partition at observation boundaries without changing global evidence IDs."""
    groups: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    size = len(json.dumps(payload["characters"], ensure_ascii=False).encode())
    for row in payload["observations"]:
        length = len(json.dumps(row, ensure_ascii=False).encode())
        if rows and size + length > byte_limit:
            groups.append({"characters": payload["characters"], "observations": rows})
            rows, size = [], 0
        rows.append(row)
        size += length
    if rows:
        groups.append({"characters": payload["characters"], "observations": rows})
    return groups


def assessment_groups(payload: dict[str, Any], candidates: list[dict[str, Any]]) -> list:
    """Check every record of each subject, without resending unrelated subjects.

    Pattern evidence validation only permits the proposed character's records.
    Their quotes and contextual evidence still include interactions with others.
    """
    by_subject: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_subject[candidate["c"]].append(candidate)
    groups = []
    for subject, proposals in by_subject.items():
        parts = windows(
            {
                **payload,
                "observations": [r for r in payload["observations"] if r["c"] == subject],
            }
        )
        for offset in range(0, len(proposals), 16):
            groups.append(({"proposals": proposals[offset : offset + 16]}, parts))
    return groups


async def synthesize(memory: Memory, execute: Any) -> None:
    payload = compact.synthesis_payload(memory)
    if len({o["order"] for o in payload["observations"]}) < 2:
        return
    parts = windows(payload)
    if len(parts) == 1:
        proposals = await execute("propose", payload, "patterns:propose")
        if proposals["proposals"]:
            assessments = await execute("assess", {**payload, **proposals}, "patterns:assess")
            compact.apply_patterns(memory, proposals, assessments)
        return
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    from app.insight.character_memory.optimized import bounded_map

    async def propose(i: int, part: dict[str, Any]) -> dict[str, Any]:
        return await execute("propose", part, f"patterns:propose:{i + 1}/{len(parts)}")

    for proposed in await bounded_map(parts, propose, concurrency=3):
        for proposal in proposed["proposals"]:
            key = json.dumps(proposal, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                candidates.append(proposal)
                seen.add(key)
    records = {row["i"]: row for row in payload["observations"]}
    # Keep schema limits and seed context bounded even for books with many traits.
    for group_no, (group, subject_parts) in enumerate(assessment_groups(payload, candidates), 1):
        seeds = {i for p in group["proposals"] for i in p["seeds"]}
        merged: list[dict[str, Any]] = [
            {"i": i, "support": set(), "counter": set()} for i in range(len(group["proposals"]))
        ]
        invalid: set[int] = set()

        async def assess(
            n: int,
            part: dict[str, Any],
            *,
            seeds: set[int] = seeds,
            group: dict[str, Any] = group,
            group_no: int = group_no,
            part_count: int = len(subject_parts),
        ) -> dict[str, Any]:
            rows = {row["i"]: row for row in part["observations"]}
            rows.update({i: records[i] for i in seeds})
            scoped = {**part, "observations": sorted(rows.values(), key=lambda r: r["i"]), **group}
            return await execute(
                "assess", scoped, f"patterns:assess:{group_no}:{n + 1}/{part_count}"
            )

        for assessed in await bounded_map(subject_parts, assess, concurrency=3):
            for audit in assessed.get("validation_audit", {}).get("invalid_pattern_references", []):
                if "assessment" in audit:
                    invalid.add(audit["assessment"]["i"])
            for result in assessed["assessments"]:
                row = merged[result["i"]]
                row["support"].update(result["support"])
                row["counter"].update(result["counter"])
        assessments = {
            "assessments": [
                {
                    "i": row["i"],
                    # Conflicting assessments of a repeated seed are withheld.
                    "support": sorted(row["support"] - row["counter"])
                    if row["i"] not in invalid
                    else [],
                    "counter": sorted(row["counter"]) if row["i"] not in invalid else [],
                }
                for row in merged
            ]
        }
        compact.apply_patterns(memory, group, assessments)
