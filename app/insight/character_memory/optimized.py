"""Durable compact memory workflow. Physical attempts, not worker slices, cost budget.

Reservations are committed before the HTTP request. Crashes may overcount an unsent
request, but cannot reset the budget or undercount a sent request. Cached steps and
registration survive retries; no automatic fallback to the legacy paid pipeline.
"""

import asyncio
import logging
import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from prolog_ai.core.llm import get_llm_config
from prolog_ai.core.request_policy import RequestPolicy, request_policy
from prolog_ai.memory import compact, repairs
from prolog_ai.memory.source import digest, validate_memory

from app.ai import jobs as ai_jobs
from app.core.config import settings
from app.insight.character_memory import checkpoints, discovery, scale
from app.jobs import service as jobs

log = logging.getLogger(__name__)

MAX_CONCURRENCY = 9


async def bounded_map(
    items: list[Any],
    fn: Any,
    *,
    stopped: asyncio.Event | None = None,
    concurrency: int = MAX_CONCURRENCY,
) -> list[Any]:
    """Stop new work on failure; drain and checkpoint already paid requests."""
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"Concurrency must be between 1 and {MAX_CONCURRENCY}")
    pending = iter(enumerate(items))
    results: list[Any] = [None] * len(items)
    stopped = stopped if stopped is not None else asyncio.Event()
    errors: list[Exception] = []

    async def worker() -> None:
        while not stopped.is_set():
            item = next(pending, None)
            if item is None:
                return
            i, value = item
            try:
                results[i] = await fn(i, value)
            except Exception as exc:
                errors.append(exc)
                stopped.set()

    workers = asyncio.gather(*(worker() for _ in range(min(concurrency, len(items)))))
    try:
        # Cancelling a to_thread waiter does not stop its HTTP request. Keep the
        # worker alive until its usage and successful result have been persisted.
        await asyncio.shield(workers)
    except asyncio.CancelledError:
        stopped.set()
        await workers
        raise
    if errors:
        raise errors[0]
    return results


class BudgetExceeded(Exception):
    pass


class CacheMiss(Exception):
    pass


class AttemptLost(Exception):
    pass


async def reserve(sessions: ai_jobs.Sessions, job_id: UUID, attempt: int, key: str) -> None:
    async with sessions() as session:
        job = await discovery.running(session, job_id, attempt)
        if not job:
            raise AttemptLost()
        result = deepcopy(job.result or {})
        state = result.setdefault("compact", {})
        attempts = state.setdefault("attempts", {})
        used = state.get("requests", 0)
        limit = state.get("request_limit", compact.MAX_REQUESTS)
        if used >= limit or attempts.get(key, 0) >= 2:
            raise BudgetExceeded()
        state["requests"] = used + 1
        attempts[key] = attempts.get(key, 0) + 1
        result.setdefault("progress", {}).update(http_requests=used + 1, request_limit=limit)
        job.result = result
        await session.commit()


async def record_usage(
    sessions: ai_jobs.Sessions,
    job_id: UUID,
    attempt: int,
    usage: dict[str, Any],
    model: str,
    key: str | None = None,
) -> None:
    from app.jobs.models import Job

    async with sessions() as session:
        # A cancelled/replaced worker may still receive an already-paid response.
        # Count its usage, but never let it update memory or successful checkpoints.
        job = await session.get(Job, job_id, with_for_update=True)
        if not job or job.attempt < attempt:
            raise AttemptLost()
        result = deepcopy(job.result or {})
        state = result.setdefault("compact", {})
        total = state.setdefault("usage", {})
        model_total = state.setdefault("usage_by_model", {}).setdefault(model, {})
        values = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "cache_write_tokens": (usage.get("input_tokens_details") or {}).get(
                "cache_write_tokens", 0
            ),
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
                "reasoning_tokens", 0
            ),
        }
        step_total = state.setdefault("usage_by_request", {}).setdefault(key or "unknown", {})
        for name, value in values.items():
            total[name] = total.get(name, 0) + (value or 0)
            model_total[name] = model_total.get(name, 0) + (value or 0)
            step_total[name] = step_total.get(name, 0) + (value or 0)
        job.result = result
        await session.commit()


async def run(
    sessions: ai_jobs.Sessions, job_id: UUID, attempt: int, ai: Any, args: dict[str, Any]
) -> str:
    from app.insight.character_memory import service

    try:
        batches = compact.plan(args["scenes"])
        planned = 2 * len(batches) + 2
        if len(batches) > scale.MAX_BATCHES:
            raise ValueError("PLAN_EXCEEDS_BUDGET")
        loop = asyncio.get_running_loop()
        async with sessions() as session:
            job = await discovery.running(session, job_id, attempt)
            if not job:
                return "lost"
            state = deepcopy((job.result or {}).get("compact") or {})
            cache = (
                {k: checkpoints.encode(v) for k, v in state.get("cache", {}).items()}
                if state.get("version") == compact.VERSION
                else {}
            )
            if state.get("version") != compact.VERSION:
                state["used_keys"] = []
            state.update(version=compact.VERSION, cache=cache, planned_steps=planned)
            state.setdefault("request_limit", scale.request_limit(len(batches)))
            state.setdefault("seed_characters", args["characters"])
            # Keep request reservations even if code/model versions changed.
            # Compact steps reconstruct the full memory after a crash. Keeping
            # that large interim object in the job multiplies it in every worker.
            result = {k: v for k, v in (job.result or {}).items() if k != "memory"}
            result.update(compact=state, progress=service.progress(None, "planning"))
            job.result = result
            await session.commit()

        batches = [
            {
                **compact.batch_payload(batch, state["seed_characters"]),
                "allow_partial": True,
                "review_actions": True,
            }
            for batch in batches
        ]
        failed = asyncio.Event()

        async def execute(
            stage: str, payload: dict[str, Any], label: str, *, cache_only: bool = False
        ) -> dict[str, Any]:
            config = get_llm_config(simple_query=stage in {"extract", "verify"})
            key = digest(
                [
                    compact.VERSION,
                    *(
                        [stage, compact.SYNTHESIS_VERSION]
                        if stage in {"propose", "assess"}
                        else [stage]
                    ),
                    config.provider,
                    config.base_url,
                    config.model,
                    config.reasoning_effort,
                    32768,
                    {k: v for k, v in payload.items() if k != "allow_partial"}
                    if stage == "verify"
                    else payload,
                ]
            )
            async with sessions() as session:
                job = await discovery.running(session, job_id, attempt)
                if not job:
                    raise AttemptLost()
                result = deepcopy(job.result or {})
                state = result["compact"]
                if cache_only and key not in state["cache"]:
                    raise CacheMiss()
                state.setdefault("request_meta", {})[key] = {
                    "stage": stage,
                    "label": label,
                    "model": config.model,
                }
                result["progress"] = {
                    **result.get("progress", service.progress(None)),
                    "stage": label,
                    "total_scenes": len(args["scenes"]),
                    "completed_requests": len(state.get("used_keys", [])),
                    "http_requests": state.get("requests", 0),
                    "request_limit": state["request_limit"],
                    "planned_requests": planned,
                }
                cached = state["cache"].get(key)
                job.result = result
                await session.commit()
            started = time.monotonic()
            if cached is not None:
                output = compact.check(stage, payload, checkpoints.decode(cached))
            else:
                if failed.is_set():
                    raise AttemptLost()

                def wait(coro: Any) -> Any:
                    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=120)

                async def reserve_if_running() -> None:
                    if failed.is_set():
                        raise AttemptLost()
                    await reserve(sessions, job_id, attempt, key)

                def invoke() -> dict[str, Any]:
                    policy = RequestPolicy(
                        before_attempt=lambda: wait(reserve_if_running()),
                        on_usage=lambda usage: wait(
                            record_usage(sessions, job_id, attempt, usage, config.model, key)
                        ),
                    )
                    with request_policy(policy):
                        return ai.run_compact_memory_step(stage, payload)

                try:
                    output = compact.check(stage, payload, await asyncio.to_thread(invoke))
                except Exception:
                    failed.set()
                    raise
            async with sessions() as session:
                job = await discovery.running(session, job_id, attempt)
                if not job:
                    raise AttemptLost()
                result = deepcopy(job.result or {})
                state = result["compact"]
                state["cache"][key] = checkpoints.encode(output)
                state["request_meta"][key].update(
                    elapsed_seconds=round(time.monotonic() - started, 3), cached=cached is not None
                )
                if stage == "extract" and label.startswith("extract:"):
                    state.setdefault("extracted_counts", {})[label] = len(
                        output.get("observations", [])
                    )
                    result["progress"]["observations"] = sum(state["extracted_counts"].values())
                state["used_keys"] = list(dict.fromkeys([*state.get("used_keys", []), key]))
                result["progress"]["completed_requests"] = len(state["used_keys"])
                job.result = result
                await session.commit()
            return output

        report: dict[str, Any] = {
            "version": discovery.VERSION,
            "processed_chunks": 0,
            "total_chunks": len(batches),
            "complete": False,
            "candidates": [],
        }

        async def repair_all(stage: str, payloads: list[dict], values: list[dict]) -> None:
            missing_for = (
                compact.missing_units if stage == "extract" else compact.missing_verifications
            )
            make_payload = (
                compact.repair_payload
                if stage == "extract"
                else compact.verification_repair_payload
            )
            merge = compact.merge_extractions if stage == "extract" else compact.merge_verifications

            def request(i: int, missing: list[int]) -> dict:
                if stage == "extract":
                    return make_payload(payloads[i], missing, values[i])
                return make_payload(payloads[i], missing)

            # Earlier attempts may already have paid for individual repairs.
            # Reuse them before grouping what remains; never reset the request cap.
            for i, value in enumerate(values):
                missing = missing_for(payloads[i], value)
                if not missing:
                    continue
                try:
                    cached = await execute(
                        stage, request(i, missing), "repair:cached", cache_only=True
                    )
                except CacheMiss:
                    continue
                values[i] = merge(payloads[i], value, cached, require_complete=False)

            for turn in range(2):
                pending = [(i, missing_for(p, values[i])) for i, p in enumerate(payloads)]
                pending = [(i, missing) for i, missing in pending if missing]
                if not pending:
                    return
                local = [request(i, missing) for i, missing in pending]
                config = get_llm_config(simple_query=True)
                cacheable = config.provider == "openai" and config.model.startswith(
                    ("gpt-6", "gpt-5.6")
                )
                if stage == "verify" and cacheable:
                    # Remapping several batches destroys their paid source prefixes.
                    # Repair in parallel against each unchanged source/target space.
                    async def repair_one(
                        n: int, payload: dict, *, turn: int = turn, pending: list = pending
                    ) -> dict:
                        return await execute(
                            stage, payload, f"verify-repair:source:{turn + 1}:{pending[n][0]}"
                        )

                    fixed = await bounded_map(
                        local,
                        repair_one,
                        stopped=failed,
                        concurrency=settings.character_memory_concurrency,
                    )
                    for (i, _), output in zip(pending, fixed, strict=True):
                        values[i] = merge(payloads[i], values[i], output, require_complete=False)
                    continue
                for offset in range(0, len(local), scale.REPAIR_BATCHES):
                    subset = local[offset : offset + scale.REPAIR_BATCHES]
                    combined, mappings = repairs.pack(stage, subset)
                    fixed = await execute(
                        stage, combined, f"{stage}-repair:combined:{turn + 1}:{offset}"
                    )
                    for (i, _), output in zip(
                        pending[offset : offset + scale.REPAIR_BATCHES],
                        repairs.unpack(stage, subset, fixed, mappings),
                        strict=True,
                    ):
                        values[i] = merge(payloads[i], values[i], output, require_complete=False)
            if any(missing_for(p, values[i]) for i, p in enumerate(payloads)):
                if stage == "extract":
                    raise ValueError("Coverage incomplete after two bounded combined repairs")
                for i, payload in enumerate(payloads):
                    values[i] = compact.defer_verifications(payload, values[i])

        async def extract_batch(i: int, batch: dict) -> dict:
            return await execute("extract", batch, f"extract:{i + 1}/{len(batches)}")

        extracts = await bounded_map(
            batches,
            extract_batch,
            stopped=failed,
            concurrency=settings.character_memory_concurrency,
        )
        await repair_all("extract", batches, extracts)
        for batch, extracted in zip(batches, extracts, strict=True):
            compact.check("extract", {**batch, "allow_partial": False}, extracted)
            report["candidates"].extend(compact.candidates(batch, extracted))
        report["processed_chunks"] = len(batches)
        batches = compact.share_discovered_people(batches, extracts)

        payloads = [
            {**compact.verification_payload(b, e), "allow_partial": True}
            for b, e in zip(batches, extracts, strict=True)
        ]

        async def verify_batch(i: int, payload: dict) -> dict:
            if not payload["observations"] and not payload["alias_names"]:
                return {"decisions": [], "bindings": []}
            return await execute("verify", payload, f"verify:{i + 1}/{len(batches)}")

        reviews = await bounded_map(
            payloads,
            verify_batch,
            stopped=failed,
            concurrency=settings.character_memory_concurrency,
        )
        await repair_all("verify", payloads, reviews)
        async with sessions() as session:
            job = await discovery.running(session, job_id, attempt)
            if not job:
                raise AttemptLost()
            result = deepcopy(job.result or {})
            result["compact"]["deferred_verifications"] = [
                {"batch": i, **audit}
                for i, review in enumerate(reviews)
                if (audit := review.get("validation_audit", {}).get("deferred_verifications"))
            ]
            job.result = result
            await session.commit()

        async def adjudicate(i: int, review: dict) -> dict:
            payload = payloads[i]
            compact.check("verify", {**payload, "allow_partial": False}, review)
            disputed = set(compact.ownership_disputes(extracts[i], review))
            if disputed:
                focused = {
                    **compact.focused_verification(payload, disputed),
                    "allow_partial": False,
                }
                checked = await execute("verify", focused, f"attribution:{i + 1}/{len(batches)}")
                review = compact.attach_adjudication(payload, review, checked)
                compact.check("verify", payload, review)
            return review

        reviews = await bounded_map(
            reviews, adjudicate, stopped=failed, concurrency=settings.character_memory_concurrency
        )
        # Apply the existing literal-name checks, full-name guard and alias review policy.
        from app.ai.character_discovery import verified

        scenes = {s["scene_id"]: s for s in args["scenes"]}
        for candidate in report["candidates"]:
            ev = candidate["evidence"]
            person = {k: candidate[k] for k in ("name", "kind", "alias_of", "quote", "reason")}
            checked = verified(
                {
                    "text": scenes[ev["scene_id"]]["text"],
                    "scene_id": ev["scene_id"],
                    "revision": ev["revision"],
                    "start": 0,
                },
                {"people": [person]},
            )
            if not checked:
                candidate["kind"] = "ambiguous"
            else:
                candidate.update(kind=checked[0]["kind"], reason=checked[0]["reason"])
        # Candidate IDs may repeat at overlapping source boundaries; retain each once.
        report["candidates"] = list({c["candidate_id"]: c for c in report["candidates"]}.values())
        async with sessions() as session:
            job = await discovery.running(session, job_id, attempt)
            if not job:
                raise AttemptLost()
            registered = (job.result or {}).get("discovery", {}).get("complete")
            if registered:
                args = job.input
        if not registered:
            args = await discovery.finalize(sessions, job_id, attempt, args, report)

        config = get_llm_config(simple_query=True)
        memory = compact.build_memory(
            args["project_id"],
            args["scenes"],
            args["characters"],
            batches,
            extracts,
            reviews,
            model=config.model,
        )
        # Publish a compressed, independently verified record snapshot while
        # pattern checks continue. Never expose unchecked extraction as ready.
        record_snapshot = memory.model_copy(update={"complete": True}).model_dump()
        validate_memory(record_snapshot, require_complete=True)
        async with sessions() as session:
            job = await discovery.running(session, job_id, attempt)
            if not job:
                return "lost"
            result = dict(job.result or {})
            result["progress"] = {
                **result.get("progress", {}),
                "stage": "synthesis",
                "processed_scenes": len(memory.processed_scene_ids),
                "total_scenes": len(memory.scenes),
                "observations": len(memory.observations),
                "verified_speakers": len(memory.speaker_verifications),
                "patterns": len(memory.patterns),
            }
            result["record_snapshot"] = checkpoints.encode(record_snapshot)
            result["records_ready_at"] = datetime.now(UTC).isoformat()
            job.result = result
            await session.commit()
        await scale.synthesize(memory, execute)
        memory.complete = True
        validate_memory(memory.model_dump(), require_complete=True)
        if not await service.persist(sessions, job_id, attempt, memory.model_dump(), "completed"):
            return "lost"
        async with sessions() as session:
            job = await discovery.running(session, job_id, attempt)
            if not job:
                return "lost"
            state = (job.result or {})["compact"]
            # Keep only the cache entries actually used by this snapshot.
            result = dict(job.result or {})
            result.pop("record_snapshot", None)
            result["compact"] = {
                **state,
                "cache": {k: state["cache"][k] for k in state.get("used_keys", [])},
            }
            job.result = result
            won = await jobs.finish(session, job_id, attempt, "completed")
            await session.commit()
            return "completed" if won else "lost"
    except AttemptLost:
        return "lost"
    except Exception as exc:
        log.exception("Compact character analysis failed: job=%s attempt=%s", job_id, attempt)
        code = (
            "AI_MEMORY_BUDGET_EXCEEDED" if isinstance(exc, BudgetExceeded) else "AI_ANALYSIS_FAILED"
        )
        if isinstance(exc, ValueError) and str(exc) == "PLAN_EXCEEDS_BUDGET":
            code = "AI_MEMORY_PLAN_TOO_LARGE"
        async with sessions() as session:
            outcome = await jobs.fail(
                session,
                job_id,
                attempt,
                {
                    "code": code,
                    "message": "분석이 중단되었습니다. 저장된 결과와 호출 예산은 유지됩니다.",
                },
                retryable=False,
            )
            await session.commit()
        return outcome or "lost"
