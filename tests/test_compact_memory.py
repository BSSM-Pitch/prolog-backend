"""Real DB checkpoints and budget reservations; all provider calls are simulated."""

import asyncio
import threading
from collections import Counter
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from prolog_ai.core.request_policy import current_policy
from prolog_ai.memory import compact

from app.content.manuscripts.models import Chapter
from app.db.session import SessionFactory
from app.insight.character_memory import optimized, service
from app.jobs import service as jobs
from tests.test_character_discovery import overview, prepare, queue


@pytest.fixture(autouse=True)
def offline_provider(monkeypatch):
    monkeypatch.setenv("PROLOG_AI_PROVIDER", "openai")
    monkeypatch.delenv("PROLOG_AI_MODEL", raising=False)


class CompactAI:
    supports_compact_memory = True

    def __init__(self, fail_verify=False):
        self.calls = []
        self.fail_verify = fail_verify

    def run_compact_memory_step(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        policy = current_policy.get()
        assert policy
        policy.before_attempt()  # Simulate a physical request, including failed attempts.
        self.calls.append(stage)
        if policy.on_usage:
            policy.on_usage(
                {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 3},
                    "output_tokens_details": {"reasoning_tokens": 1},
                }
            )
        if stage == "extract":
            targets = payload["target_ids"]
            return {
                "people": [
                    {
                        "name": "서윤",
                        "kind": "named_person",
                        "alias_of": None,
                        "e": targets[0],
                        "reason": "발화 지문",
                    }
                ],
                "observations": [
                    {
                        "c": "서윤",
                        "e": [i],
                        "x": [],
                        "k": "speech",
                        "p": "depicted",
                        "b": "other",
                        "a": "certain",
                        "to": [],
                    }
                    for i in targets
                ],
                "covered_units": targets,
            }
        if stage == "verify":
            if self.fail_verify:
                self.fail_verify = False
                raise RuntimeError("Simulated response loss")
            return {
                "decisions": [
                    {"i": o["i"], "c": "서윤", "a": "certain", "e": o["e"], "reason": "명시된 이름"}
                    for o in payload["observations"]
                ]
            }
        if stage == "propose":
            return {
                "proposals": [
                    {
                        "c": payload["observations"][0]["c"],
                        "description": "괜찮아라는 표현",
                        "condition": "대답할 때",
                        "seeds": [o["i"] for o in payload["observations"]],
                        "hypothesis": None,
                        "alternatives": [],
                    }
                ]
            }
        assert stage == "assess"
        return {
            "assessments": [
                {"i": 0, "support": [o["i"] for o in payload["observations"]], "counter": []}
            ]
        }


async def chapters(m, count=2):
    async with SessionFactory() as session:
        for i in range(1, count + 1):
            session.add(
                Chapter(
                    project_id=UUID(m["project"].split("/")[-1]),
                    manuscript_id=UUID(m["base"].split("/")[-1]),
                    chapter_no=i,
                    content=f"서윤은 말했다. “괜찮아.” {i}\n",
                )
            )
        await session.commit()


async def saved(aid):
    async with SessionFactory() as session:
        job = await jobs.get(session, aid)
        return deepcopy(job.result)


async def test_unified_discovery_memory_patterns_and_same_snapshot_reuse(client: AsyncClient):
    m = await prepare(client, "compact@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m)
    aid = await queue(client, m)
    ai = CompactAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify", "propose", "assess"]
    result = await saved(aid)
    assert result["compact"]["requests"] == 4
    assert result["compact"]["usage"]["input_tokens"] == 40
    assert result["compact"]["usage_by_model"]["gpt-6-luna"]["input_tokens"] == 20
    assert result["compact"]["usage_by_model"]["gpt-6.1-terra"]["input_tokens"] == 20
    assert len(result["memory"]["characters"]) == 1
    assert len(result["memory"]["patterns"]) == 1
    assert result["memory"]["complete"]
    state = await overview(client, m)
    assert state["job"]["progress"]["http_requests"] == 4
    assert state["job"]["progress"]["request_limit"] == 40
    assert await queue(client, m) == aid
    assert ai.calls == ["extract", "verify", "propose", "assess"]


async def test_resume_reuses_extraction_and_registers_once_after_review(client: AsyncClient):
    m = await prepare(client, "compact-resume@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)
    ai = CompactAI(fail_verify=True)
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    result = await saved(aid)
    assert result["compact"]["requests"] == 2
    assert not result["discovery"]["complete"]
    assert (
        await client.post(f"{m['base']}/character-memory/{aid}/retry", headers=m["h"])
    ).status_code == 202
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify", "verify"]
    result = await saved(aid)
    assert result["compact"]["requests"] == 3
    assert len(result["memory"]["characters"]) == 1


async def test_concurrent_reservations_never_send_request_41_and_retry_cannot_reset(
    client: AsyncClient,
):
    m = await prepare(client, "compact-budget@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)
    async with SessionFactory() as session:
        job = await jobs.claim(session, aid)
        attempt = job.attempt
        job.result = {**job.result, "compact": {"requests": 39, "attempts": {}}}
        await session.commit()
    results = await asyncio.gather(
        optimized.reserve(SessionFactory, aid, attempt, "a"),
        optimized.reserve(SessionFactory, aid, attempt, "b"),
        return_exceptions=True,
    )
    assert sum(isinstance(x, optimized.BudgetExceeded) for x in results) == 1
    assert (await saved(aid))["compact"]["requests"] == 40
    async with SessionFactory() as session:
        await jobs.finish(session, aid, attempt, "failed")
        job = await jobs.get(session, aid)
        await jobs.retry(session, aid, by_user=True)
        await session.commit()
    ai = CompactAI()
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    assert not ai.calls
    assert (await saved(aid))["compact"]["requests"] == 40
    state = await overview(client, m)
    assert state["job"]["error"]["code"] == "AI_MEMORY_BUDGET_EXCEEDED"
    assert await queue(client, m) == aid  # a repeated create must not reset the allowance
    assert (
        await client.post(f"{m['base']}/character-memory/{aid}/retry", headers=m["h"])
    ).status_code == 400


async def test_changed_chapter_reuses_unaffected_batches(client: AsyncClient):
    from sqlalchemy import select

    m = await prepare(client, "compact-change@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m, count=6)
    first = await queue(client, m)
    ai = CompactAI()
    assert await service.handle(SessionFactory, first, ai) == "completed"
    assert ai.calls.count("extract") == 3
    async with SessionFactory() as session:
        chapter = await session.scalar(select(Chapter).where(Chapter.chapter_no == 1))
        chapter.content = "서윤은 다시 말했다. “괜찮아.” 1\n"
        await session.commit()
    second = await queue(client, m)
    assert second != first
    ai = CompactAI()
    assert await service.handle(SessionFactory, second, ai) == "completed"
    # The first refresh adds registered IDs to the shared identity context.
    # Recheck all affected verification inputs before reusing later snapshots.
    assert Counter(ai.calls) == {"extract": 3, "verify": 3, "propose": 1, "assess": 1}
    assert ai.calls[-2:] == ["propose", "assess"]
    async with SessionFactory() as session:
        chapter = await session.scalar(select(Chapter).where(Chapter.chapter_no == 1))
        chapter.content = "서윤은 조용히 말했다. “괜찮아.” 1\n"
        await session.commit()
    third = await queue(client, m)
    assert third != second
    ai = CompactAI()
    assert await service.handle(SessionFactory, third, ai) == "completed"
    # Extraction reuses unaffected batches. Shared identity evidence changed,
    # so independent ownership verification must see the updated context.
    assert Counter(ai.calls) == {"extract": 1, "verify": 3, "propose": 1, "assess": 1}
    assert (await saved(third))["compact"]["requests"] == 6
    async with SessionFactory() as session:
        chapter = await session.scalar(select(Chapter).where(Chapter.chapter_no == 2))
        chapter.content += "서윤은 돌아섰다.\n"
        await session.commit()
    fourth = await queue(client, m)
    ai = CompactAI()
    assert await service.handle(SessionFactory, fourth, ai) == "completed"
    # Changing the actual neighboring context invalidates that dependent batch.
    assert Counter(ai.calls) == {"extract": 2, "verify": 3, "propose": 1, "assess": 1}
    assert ai.calls[-2:] == ["propose", "assess"]
    assert (await saved(fourth))["compact"]["requests"] == 7


async def test_oversized_plan_fails_before_any_paid_request(client: AsyncClient, monkeypatch):
    m = await prepare(client, "compact-size@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)
    monkeypatch.setattr(compact, "MAX_STEPS", 1)
    ai = CompactAI()
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    assert not ai.calls
    assert (await overview(client, m))["job"]["error"]["code"] == "AI_MEMORY_PLAN_TOO_LARGE"


async def test_parallel_calls_keep_all_usage_and_cache_entries(client: AsyncClient):
    m = await prepare(client, "compact-parallel@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m, count=18)
    aid = await queue(client, m)

    class ConcurrentAI(CompactAI):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(9, timeout=10)
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def run_compact_memory_step(self, stage, payload):
            if stage not in {"extract", "verify"}:
                return super().run_compact_memory_step(stage, payload)
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.barrier.wait()
                return super().run_compact_memory_step(stage, payload)
            finally:
                with self.lock:
                    self.active -= 1

    ai = ConcurrentAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    r = (await saved(aid))["compact"]
    assert ai.peak == 9
    assert r["requests"] == len(r["cache"]) == len(r["used_keys"]) == 20
    assert r["usage"]["input_tokens"] == 200
    assert r["usage"]["output_tokens"] == 40


async def test_bounded_workers_drain_after_failure_and_preserve_order():
    entered, finished = [], []
    ready = asyncio.Event()

    async def failing(i, value):
        entered.append(i)
        if len(entered) == 3:
            ready.set()
        await ready.wait()
        if i == 0:
            raise ValueError("stop")
        await asyncio.sleep(0.01)
        finished.append(i)
        return value

    with pytest.raises(ValueError, match="stop"):
        await optimized.bounded_map(list(range(9)), failing, concurrency=3)
    assert entered == [0, 1, 2]
    assert sorted(finished) == [1, 2]  # Paid work finished before the job fails.

    async def reversed_finish(i, value):
        await asyncio.sleep((3 - i) * 0.001)
        return value

    assert await optimized.bounded_map([10, 20, 30], reversed_finish) == [10, 20, 30]


async def test_missing_tail_gets_one_targeted_repair_and_all_speakers_reviewed(client: AsyncClient):
    m = await prepare(client, "compact-repair@example.com", "서윤은 말했다. “괜찮아.”")

    class MissingTailAI(CompactAI):
        def __init__(self):
            super().__init__()
            self.repaired = []

        def run_compact_memory_step(self, stage, payload):
            output = super().run_compact_memory_step(stage, payload)
            if stage == "extract" and payload.get("repair"):
                self.repaired = payload["target_ids"]
                # The name is in contextual unit 0, but repair targets contain the
                # dialogue only. Use the known full name provided in the roster.
                output["people"] = []
            elif stage == "extract":
                output["observations"] = output["observations"][:1]
                # A claimed full covered_units list must not mask the omission.
            return output

    # Seed an author-owned name so a dialogue-only repair can use its ID.
    from app.authoring.ass.models import Character

    async with SessionFactory() as session:
        session.add(Character(project_id=UUID(m["project"].split("/")[-1]), name="서윤"))
        await session.commit()
    aid = await queue(client, m)
    ai = MissingTailAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "extract", "verify"]
    assert ai.repaired == [1]
    result = await saved(aid)
    assert len(result["memory"]["observations"]) == 2
    assert len(result["memory"]["speaker_verifications"]) == 2


async def test_pipeline_version_change_does_not_return_old_completed_analysis(
    client: AsyncClient, monkeypatch
):
    m = await prepare(client, "compact-version@example.com", "서윤은 말했다. “괜찮아.”")
    first = await queue(client, m)
    assert await service.handle(SessionFactory, first, CompactAI()) == "completed"
    monkeypatch.setattr(compact, "VERSION", "next-version")
    second = await queue(client, m)
    assert second != first
    ai = CompactAI()
    assert await service.handle(SessionFactory, second, ai) == "completed"
    assert ai.calls == ["extract", "verify"]


async def test_cancellation_drains_workers_without_starting_queued_work():
    started, finished = [], []
    ready, release = asyncio.Event(), asyncio.Event()

    async def work(i, value):
        started.append(i)
        if len(started) == 3:
            ready.set()
        await release.wait()
        finished.append(i)
        return value

    task = asyncio.create_task(optimized.bounded_map(list(range(6)), work, concurrency=3))
    await ready.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert started == finished == [0, 1, 2]


async def test_non_speech_owner_disagreement_uses_one_checkpointed_recheck(client: AsyncClient):
    m = await prepare(
        client, "compact-owner@example.com", "서윤은 하린을 떠올렸다. 하린은 휘파람을 불었다."
    )
    aid = await queue(client, m)

    class OwnerAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            out = super().run_compact_memory_step(stage, payload)
            if stage == "extract":
                out["people"].append(
                    {
                        "name": "하린",
                        "kind": "named_person",
                        "alias_of": None,
                        "e": 1,
                        "reason": "행동 지문",
                    }
                )
                for record in out["observations"]:
                    record["k"] = "narration"
            elif stage == "verify":
                for d in out["decisions"]:
                    if d["i"] == 1:
                        d["c"] = "하린"
            return out

    ai = OwnerAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify", "verify"]
    r = await saved(aid)
    assert r["compact"]["requests"] == 3
    names = {c["character_id"]: c["name"] for c in r["memory"]["characters"]}
    assert names[r["memory"]["observations"][1]["character_id"]] == "하린"
    assert not r["memory"]["speaker_verifications"]


async def test_late_paid_usage_is_counted_after_attempt_is_closed(client: AsyncClient):
    m = await prepare(client, "compact-late-usage@example.com", "서윤은 말했다.")
    aid = await queue(client, m)
    async with SessionFactory() as session:
        job = await jobs.claim(session, aid)
        attempt = job.attempt
        await session.commit()
    await optimized.reserve(SessionFactory, aid, attempt, "inflight")
    async with SessionFactory() as session:
        await jobs.finish(session, aid, attempt, "failed")
        await session.commit()
    await optimized.record_usage(
        SessionFactory, aid, attempt, {"input_tokens": 50, "output_tokens": 5}, "gpt-6-luna"
    )
    result = await saved(aid)
    assert result["compact"]["requests"] == 1
    assert result["compact"]["usage"]["input_tokens"] == 50
    async with SessionFactory() as session:
        assert (await jobs.get(session, aid)).status == "failed"


async def test_first_pass_verification_waits_for_shared_discovered_names(client: AsyncClient):
    m = await prepare(client, "compact-first-pass@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m, count=4)
    aid = await queue(client, m)

    class SharedRosterAI(CompactAI):
        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()
            self.extract_count = 0
            self.review_count = 0

        def run_compact_memory_step(self, stage, payload):
            if stage == "extract":
                with self.lock:
                    self.extract_count += 1
            if stage == "verify":
                assert self.extract_count == 2
                assert "서윤" in {p["name"] for p in payload["known_people"]}
                self.review_count += 1
            return super().run_compact_memory_step(stage, payload)

    ai = SharedRosterAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.review_count == 2
    result = await saved(aid)
    assert result["discovery"]["complete"]
    assert len(result["memory"]["characters"]) == 1
    assert result["compact"]["requests"] == 6


@pytest.mark.parametrize("concurrency", [0, 10])
async def test_invalid_concurrency_never_starts_work(concurrency):
    async def unexpected(i, value):
        pytest.fail("Invalid concurrency started work")

    with pytest.raises(ValueError, match="Concurrency"):
        await optimized.bounded_map([1], unexpected, concurrency=concurrency)


async def test_partial_review_reuses_good_decisions_and_repairs_only_missing(client: AsyncClient):
    m = await prepare(client, "compact-review-repair@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)

    class PartialReviewAI(CompactAI):
        repair_ids = None

        def run_compact_memory_step(self, stage, payload):
            output = super().run_compact_memory_step(stage, payload)
            if stage == "verify":
                if payload.get("repair"):
                    self.repair_ids = [o["i"] for o in payload["observations"]]
                else:
                    output["decisions"] = output["decisions"][:1]
            return output

    ai = PartialReviewAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify", "verify"]
    assert ai.repair_ids == [1]  # Only the missing observation is sent; its ID is preserved.
    result = await saved(aid)
    assert len(result["memory"]["observations"]) == 2
    assert len(result["memory"]["speaker_verifications"]) == 2
    assert result["compact"]["requests"] == 3


async def test_exhausted_proof_repairs_finish_with_audited_uncertainty(client: AsyncClient):
    m = await prepare(client, "compact-deferred@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)

    class InvalidProofAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            output = super().run_compact_memory_step(stage, payload)
            if stage == "verify":
                output["validation_audit"] = {
                    "invalid_proof_rows": [[d["i"], 1, 999, 1] for d in output["decisions"]]
                }
            return output

    ai = InvalidProofAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify", "verify"]  # Identical second repair reuses cache.
    result = await saved(aid)
    assert result["compact"]["requests"] == 3
    assert result["compact"]["deferred_verifications"][0]["indices"] == [0, 1]
    records = result["memory"]["observations"]
    assert len(records) == 2
    assert all(o["attribution"] == "uncertain" for o in records)
    assert all("검증 미확정" in o["attribution_checks"][-1]["reason"] for o in records)


async def test_exhausted_extraction_coverage_still_fails(client: AsyncClient):
    m = await prepare(client, "compact-uncovered@example.com", "서윤은 말했다. “괜찮아.”")
    aid = await queue(client, m)

    class UncoveredAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            output = super().run_compact_memory_step(stage, payload)
            assert stage == "extract"
            output.update(people=[], observations=[], covered_units=[], skipped_units=[])
            return output

    ai = UncoveredAI()
    assert await service.handle(SessionFactory, aid, ai) == "failed"
    assert ai.calls == ["extract", "extract"]  # Identical repair reuses cache.
    assert not (await saved(aid)).get("memory", {}).get("complete")


async def test_three_batches_share_one_sparse_extraction_repair(client: AsyncClient):
    m = await prepare(client, "compact-combined-repair@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m, count=6)
    aid = await queue(client, m)

    class SparseAI(CompactAI):
        repair_targets = None

        def run_compact_memory_step(self, stage, payload):
            result = super().run_compact_memory_step(stage, payload)
            if stage == "extract":
                if payload.get("repair"):
                    self.repair_targets = payload["target_ids"]
                    result["people"] = []  # Already discovered before the sparse repair.
                else:
                    result["observations"] = result["observations"][:-1]
            return result

    ai = SparseAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert len(ai.repair_targets) == 3
    assert ai.calls.count("extract") == 4  # Three initial requests and one shared repair.
    result = await saved(aid)
    assert result["compact"]["requests"] == 9  # 3 + 1 + 3 + 2 synthesis calls.
    assert len(result["memory"]["processed_scene_ids"]) == 6


@pytest.mark.parametrize("verified_name", ["하린", "어머니", "서윤"])
async def test_scoped_identity_never_adds_a_model_call(client: AsyncClient, verified_name):
    from app.authoring.ass.models import Character

    m = await prepare(
        client, "scoped-identity@example.com", "서윤의 어머니는 하린이다. 어머니는 휘파람을 불었다."
    )
    async with SessionFactory() as session:
        for name in ["서윤", "하린", "어머니"]:
            session.add(Character(project_id=UUID(m["project"].split("/")[-1]), name=name))
        await session.commit()
    aid = await queue(client, m)

    class IdentityAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            output = super().run_compact_memory_step(stage, payload)
            if stage == "extract":
                output["people"] = []
                output["observations"][1].update(
                    c="하린",
                    k="action",
                    identity={"reference": "어머니", "relative_to": "서윤", "evidence": [0, 1]},
                )
            elif stage == "verify":
                output["decisions"][1]["c"] = verified_name
            return output

    ai = IdentityAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert ai.calls == ["extract", "verify"]
    result = await saved(aid)
    assert result["compact"]["requests"] == 2
    record = result["memory"]["observations"][1]
    names = {c["character_id"]: c["name"] for c in result["memory"]["characters"]}
    confirmed = verified_name in {"하린", "어머니"}
    assert names.get(record["character_id"]) == ("하린" if confirmed else None)
    assert record["attribution"] == ("certain" if confirmed else "uncertain")


async def test_current_analysis_does_not_import_previous_verdicts(
    client: AsyncClient,
):
    from app.insight.character_memory import discovery

    m = await prepare(client, "compact-history@example.com", "서윤은 말했다. “괜찮아.”")
    first_id = await queue(client, m)
    assert await service.handle(SessionFactory, first_id, CompactAI()) == "completed"
    async with SessionFactory() as session:
        first = await jobs.get(session, first_id)
        second = await jobs.create(
            session,
            project_id=first.project_id,
            job_type=first.job_type,
            target_type=first.target_type,
            target_id=first.target_id,
            queue=first.queue,
            created_by=first.created_by,
            input=deepcopy(first.input),
        )
        second.result = {"discovery": discovery.initial(second.input)}
        second_id = second.id
        await session.commit()

    class UncertainReviewAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            result = super().run_compact_memory_step(stage, payload)
            if stage == "verify":
                for d in result["decisions"]:
                    d.update(c=None, a="uncertain")
            return result

    ai = UncertainReviewAI()
    assert await service.handle(SessionFactory, second_id, ai) == "completed"
    result = await saved(second_id)
    assert ai.calls == ["extract", "verify"]
    assert result["compact"]["requests"] == 2
    assert "review_history" not in result["compact"]
    assert all(o["attribution"] == "uncertain" for o in result["memory"]["observations"])
    assert (await saved(first_id))["compact"]["requests"] == 2


async def test_eighteen_chapter_cold_start_has_no_history_and_twenty_requests(
    client: AsyncClient, monkeypatch
):
    from prolog_ai.memory import review_history

    def forbidden(*args, **kwargs):
        pytest.fail("First-pass analysis must not import a previous verdict")

    monkeypatch.setattr(review_history, "reuse_reviews", forbidden)
    m = await prepare(client, "compact-cold-18@example.com", "서윤은 말했다. “괜찮아.”")
    await chapters(m, count=18)
    aid = await queue(client, m)
    ai = CompactAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    assert Counter(ai.calls) == {"extract": 9, "verify": 9, "propose": 1, "assess": 1}
    result = await saved(aid)
    assert result["compact"]["seed_characters"] == []
    assert result["compact"]["requests"] == 20
    assert "review_history" not in result["compact"]
    assert result["memory"]["complete"]


async def test_cold_first_upload_links_short_name_across_batches_without_extra_calls(client):
    from prolog_ai.memory import compact_wire

    m = await prepare(client, "compact-identity-cold@example.com", "한민서는 걸었다.")
    async with SessionFactory() as session:
        for i, text in enumerate(
            [
                "한민서는 걸었다. 민서는 웃었다.",
                "한민서는 손을 들었다.",
                "민서는 손을 떨었다.",
                "민서는 숨을 골랐다.",
            ],
            1,
        ):
            session.add(
                Chapter(
                    project_id=UUID(m["project"].split("/")[-1]),
                    manuscript_id=UUID(m["base"].split("/")[-1]),
                    chapter_no=i,
                    content=text,
                )
            )
        await session.commit()
    aid = await queue(client, m)

    class ColdIdentityAI(CompactAI):
        def run_compact_memory_step(self, stage, payload):
            if stage not in {"extract", "verify"}:
                return super().run_compact_memory_step(stage, payload)
            current_policy.get().before_attempt()
            self.calls.append(stage)
            if stage == "extract":
                assert payload["known_people"] == []
                units = [u for u in payload["units"] if u["id"] in payload["target_ids"]]
                names = sorted({"한민서" if "한민서" in u["text"] else "민서" for u in units})
                raw = {
                    "people": [
                        {
                            "name": n,
                            "alias": -1,
                            "u": next(u["id"] for u in units if n in u["text"]),
                        }
                        for n in names
                    ],
                    "rows": [
                        [
                            u["id"],
                            names.index("한민서" if "한민서" in u["text"] else "민서"),
                            1,
                            0,
                            0,
                            1,
                        ]
                        for u in units
                    ],
                    "skip": [],
                }
                return compact.validate_extraction(
                    payload, compact_wire.extraction_response(payload, raw)
                )
            units = {u["id"]: u for u in payload["units"]}
            original_full = any(
                "한민서" in units[o["e"][0]]["text"] for o in payload["observations"]
            )
            return {
                "decisions": [
                    {
                        "i": o["i"],
                        "c": "한민서" if "한민서" in units[o["e"][0]]["text"] else "민서",
                        "a": "certain",
                        "e": o["e"],
                        "reason": "독립 원문 판정",
                    }
                    for o in payload["observations"]
                ],
                "bindings": [
                    {
                        "name": "민서",
                        "canonical": "한민서" if original_full else "민서",
                        "e": next(u["id"] for u in units.values() if "민서" in u["text"]),
                    }
                ]
                if "민서" in payload["alias_names"]
                else [],
            }

    ai = ColdIdentityAI()
    assert await service.handle(SessionFactory, aid, ai) == "completed"
    result = await saved(aid)
    assert result["compact"]["seed_characters"] == []
    assert Counter(ai.calls) == {"extract": 2, "verify": 3, "propose": 1, "assess": 1}
    assert result["compact"]["requests"] == 6
    memory = result["memory"]
    assert [p["name"] for p in memory["characters"]] == ["한민서"]
    person = memory["characters"][0]["character_id"]
    assert all(
        o["character_id"] == person and o["attribution"] == "certain"
        for o in memory["observations"]
    )
    assert any(o["identity_links"] for o in memory["observations"])
    assert all(
        c["status"] == "existing" and c["character_id"] == person
        for c in result["discovery"]["candidates"]
        if c["name"] == "민서"
    )
    details = await service_details_for_test(m, person)
    assert details["analysis_id"] == str(aid)


async def service_details_for_test(m, person):
    async with SessionFactory() as session:
        return await service.details(
            session,
            UUID(m["project"].split("/")[-1]),
            UUID(m["base"].split("/")[-1]),
            UUID(person),
            None,
        )
