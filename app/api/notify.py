"""Outbox 이벤트 → 알림.

수신자를 찾으려면 `invited_email` 로 **AUTH** 를 조회해야 하고, 알림을 만들려면 **NOTI** 를
호출해야 한다. 두 모듈이 필요하므로 조합 레이어의 일이다(모듈끼리는 서로를 못 부른다, 규칙 3).

릴레이가 큐로 보낸 메시지를 워커가 받아 이 함수에 넣는다.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import inbox
from app.platform_.auth import service as auth
from app.platform_.notifications import service as notifications
from app.platform_.notifications.schemas import NotificationResponse

log = logging.getLogger(__name__)

CONSUMER = "notifier"

# 이벤트 → (알림 type, related_ref.type, 제목, 본문)
INVITE_EVENTS: dict[str, tuple[str, str, str, str]] = {
    "team.invited": ("team_invite", "team_invitation", "팀 초대", "새 팀 초대가 도착했습니다."),
    "project.invited": (
        "project_invite",
        "project_invitation",
        "프로젝트 초대",
        "새 프로젝트 초대가 도착했습니다.",
    ),
}


async def notify_from_event(
    session: AsyncSession, event: dict[str, Any]
) -> NotificationResponse | None:
    """만들었으면 알림을, 대상이 없거나 다룰 이벤트가 아니면 None 을 돌려준다."""
    mapping = INVITE_EVENTS.get(str(event.get("event_type")))
    if mapping is None:
        return None
    noti_type, ref_type, title, body = mapping

    payload = event.get("payload") or {}
    email = payload.get("invited_email")
    if not email:
        log.warning("초대 이벤트에 invited_email 이 없다: %s", event.get("event_id"))
        return None

    # 미가입 이메일이면 인앱 알림 대상이 없다. 명세 §4.7 비고의 "회원가입 유도 알림" 은
    # 메일 채널이라 발송 경로가 생긴 뒤의 일이다.
    user_id = await auth.find_user_id_by_email(session, str(email))
    if user_id is None:
        return None

    return await notifications.create(
        session,
        user_id=user_id,
        type=noti_type,
        title=title,
        body=body,
        resource_type=ref_type,
        resource_id=UUID(str(payload["invitation_id"])),
        payload=payload,
    )


async def consume(session: AsyncSession, event: dict[str, Any]) -> NotificationResponse | None:
    """notifier 워커의 메시지 하나. **같은 이벤트는 한 번만** 알림이 된다.

    릴레이가 at-least-once 라 같은 outbox 이벤트가 두 번 올 수 있다(durability.py dup).
    알림 INSERT 와 같은 트랜잭션에서 event_id 를 선점하므로, 중간에 실패해 롤백되면
    선점도 사라져 재수신 때 다시 처리된다. `event_id` 가 없거나 깨졌으면 예외 — 소비 루프가
    메시지를 남기고, 반복 실패하면 DLQ 로 간다.
    """
    event_id = UUID(str(event["event_id"]))
    if not await inbox.first_time(session, CONSUMER, event_id):
        log.info("이미 처리한 이벤트 %s — 건너뛴다", event_id)
        return None
    return await notify_from_event(session, event)
