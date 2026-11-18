"""Composition: validate project/character ownership before storing an image."""

import asyncio
import logging
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.authoring.ass.models import Character
from app.content.media.images import MAX_BYTES, ImageStorage, image_storage, normalize
from app.content.media.models import ProjectImage
from app.core import errors
from app.core.deps import require_project_role
from app.core.response import Envelope, ok, raises
from app.db.session import Session
from app.platform_.projects.models import Project

router = APIRouter(prefix="/projects", tags=["media"])
ProjectId = Annotated[UUID, Path(alias="projectId")]
TargetId = Annotated[UUID, Path(alias="targetId")]
Kind = Literal["portrait"]
Store = Annotated[ImageStorage, Depends(image_storage)]
Viewer = Depends(require_project_role("viewer"))
Editor = Depends(require_project_role("editor"))
ALL = "/{projectId}/media"
ONE = ALL + "/portrait/{targetId}"
logger = logging.getLogger(__name__)


class ImageResponse(BaseModel):
    target_id: UUID
    kind: Kind
    url: str
    width: int
    height: int


def view(row: ProjectImage, store: ImageStorage) -> dict[str, Any]:
    return {
        "target_id": row.target_id,
        "kind": row.kind,
        "url": store.url(row.storage_key),
        "width": row.width,
        "height": row.height,
    }


@router.get(
    ALL,
    dependencies=[Viewer],
    response_model=Envelope[list[ImageResponse]],
    summary="인물 사진 목록",
)
async def images(project_id: ProjectId, session: Session, store: Store) -> dict[str, Any]:
    """viewer 이상. URL은 한 시간 동안 유효하며 목록을 다시 읽으면 갱신된다."""
    rows = await session.scalars(
        select(ProjectImage).where(
            ProjectImage.project_id == project_id, ProjectImage.kind == "portrait"
        )
    )
    return ok([view(row, store) for row in rows])


async def target(session: Session, project_id: UUID, target_id: UUID) -> None:
    # Serialize writes, including the first upload when no image row exists yet.
    await session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    person = await session.scalar(
        select(Character)
        .where(
            Character.id == target_id,
            Character.project_id == project_id,
        )
        .with_for_update()
    )
    if person is None:
        raise errors.CharacterNotFound()


async def discard(store: ImageStorage, key: str) -> None:
    try:
        await asyncio.to_thread(store.delete, key)
    except Exception:
        logger.warning("Could not remove replaced project image", exc_info=True)


@router.put(
    ONE,
    summary="인물 사진 저장",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in ("image/jpeg", "image/png", "image/webp")
            },
        }
    },
    dependencies=[Editor],
    response_model=Envelope[ImageResponse],
    responses=raises(errors.CharacterNotFound),
)
async def upload(
    project_id: ProjectId,
    target_id: TargetId,
    request: Request,
    session: Session,
    store: Store,
) -> dict[str, Any]:
    """editor 이상. 최대 5MB의 이미지 원본을 요청 본문으로 보낸다. WebP로 정규화한다."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise errors.InvalidInput("이미지는 5MB 이하로 선택해 주세요.")
    normalized, width, height = await asyncio.to_thread(normalize, bytes(data))
    await target(session, project_id, target_id)
    row = await session.scalar(
        select(ProjectImage).where(
            ProjectImage.project_id == project_id,
            ProjectImage.kind == "portrait",
            ProjectImage.target_id == target_id,
        )
    )
    old_key = row.storage_key if row else None
    new_key = f"images/{project_id}/portrait/{target_id}/{uuid4()}.webp"
    await asyncio.to_thread(store.put, new_key, normalized)
    try:
        if row is None:
            row = ProjectImage(project_id=project_id, kind="portrait", target_id=target_id)
            session.add(row)
        row.storage_key, row.width, row.height = new_key, width, height
        result = view(row, store)
        await session.commit()
    except Exception:
        await discard(store, new_key)
        raise
    if old_key:
        await discard(store, old_key)
    return ok(result)


@router.delete(
    ONE,
    dependencies=[Editor],
    status_code=204,
    responses=raises(errors.CharacterNotFound),
    summary="인물 사진을 기본 표시로 되돌린다",
)
async def remove(
    project_id: ProjectId, target_id: TargetId, session: Session, store: Store
) -> None:
    """editor 이상. 기존 이미지가 없어도 성공하며, 원본 작품이나 인물은 유지된다."""
    await target(session, project_id, target_id)
    row = await session.scalar(
        select(ProjectImage).where(
            ProjectImage.project_id == project_id,
            ProjectImage.kind == "portrait",
            ProjectImage.target_id == target_id,
        )
    )
    if row:
        key = row.storage_key
        await session.delete(row)
        await session.commit()
        await discard(store, key)
