"""NLCD 제출 · 전달(forward) — NLCD 와 ASS 를 함께 부른다.

- 제출: `target_character_id` 가 확정 캐릭터인지 **ASS** 에 묻고, **NLCD** 잡을 만든다.
- 전달: **NLCD** 잡을 잠가 상태를 확인하고, **ASS** 초안을 만들고, 잡에 초안 id 를 남긴다.
  한 트랜잭션이다 — 초안만 생기고 잡에 표시가 안 되는 일은 없다.

둘 다 Ring 3 이라 서로를 부를 수 없다(규칙 3). 조합 레이어의 일이다.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.authoring.ass import service as ass
from app.authoring.nlcd import service as nlcd
from app.authoring.nlcd.schemas import (
    ExtractionCreate,
    ExtractionResponse,
    ForwardResponse,
    items_of,
)
from app.core import errors
from app.core.deps import ProjectContext, require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session

router = APIRouter(prefix="/projects", tags=["NLCD"])

ProjectId = Annotated[UUID, Path(alias="projectId")]
ExtractionId = Annotated[UUID, Path(alias="extractionId")]
EditorCtx = Annotated[ProjectContext, Depends(require_project_role("editor"))]
DUPLICATE_NOTICE = (
    "이전에 유사한 문장을 입력한 기록이 있습니다. 추출이 완료되면 병합 여부를 확인해 주세요."
)


@router.post(
    "/{projectId}/nl-extractions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="자연어 서술을 제출하고 추출을 요청한다",
    response_model=Envelope[ExtractionResponse],
    responses=raises(errors.TargetCharacterNotFound),
)
async def submit(
    project_id: ProjectId, body: ExtractionCreate, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """비동기다. 응답의 `status` 는 `analyzing` — 상세 조회로 폴링한다.

    `target_character_id` 는 기존 캐릭터에 덧붙여 입력할 때 준다(이 프로젝트의 확정 캐릭터가
    아니면 `TARGET_CHARACTER_NOT_FOUND`). 같은 문장을 전에 넣었으면 `duplicate_of` 와
    `meta.notice` 가 온다 — 추출은 막지 않는다.
    """
    if body.target_character_id is not None:
        if await ass.character_name(session, project_id, body.target_character_id) is None:
            raise errors.TargetCharacterNotFound()
    job = await nlcd.submit(
        session, project_id, body.source_text, body.target_character_id, ctx.user.id
    )
    data = nlcd.to_response(job)
    return ok(data, {"notice": DUPLICATE_NOTICE} if data.duplicate_of else {})


@router.post(
    "/{projectId}/nl-extractions/{extractionId}/forward",
    status_code=status.HTTP_201_CREATED,
    summary="추출 결과로 캐릭터 초안을 만든다",
    response_model=Envelope[ForwardResponse],
    responses=raises(errors.ExtractionNotFound, errors.ExtractionNotReady, errors.AlreadyForwarded),
)
async def forward(
    project_id: ProjectId, extraction_id: ExtractionId, session: Session, ctx: EditorCtx
) -> dict[str, Any]:
    """ASS 초안을 만든다(`source_job_id` = 이 추출, 항목은 전부 `ai_extracted` + 근거).

    - `completed` 가 아니면 `EXTRACTION_NOT_READY`, 이미 전달했으면 `ALREADY_FORWARDED`
      (`details.forwarded_draft_id`).
    - `target_character_id` 가 있는 추출은 초안 이름을 그 캐릭터 이름으로 채운다 — 확정하면 같은
      이름이라 `DUPLICATE_CHARACTER_CANDIDATE` 가 나고, 사용자가 병합을 고른다.
    - 없으면 이름이 비어 있다. 추출은 이름을 뽑지 않는다 — 확정 전에 이름을 넣어야 한다.
    """
    job = await nlcd.for_forward(session, project_id, extraction_id)
    target = job.input.get("target_character_id")
    name = await ass.character_name(session, project_id, UUID(target)) if target else None
    draft = await ass.create_draft_from_extraction(
        session,
        project_id,
        job_id=job.id,
        source_text=str(job.input["source_text"]),
        name=name,
        extracted=items_of(job.result),
        user_id=ctx.user.id,
    )
    nlcd.mark_forwarded(job, draft.draft_id)
    return ok(ForwardResponse(extraction_id=job.id, forwarded_draft_id=draft.draft_id))
