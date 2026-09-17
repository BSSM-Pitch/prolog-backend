"""도메인 예외 → 에러코드 · HTTP 상태 매핑.

에러 코드 문자열은 명세에 적힌 값을 그대로 쓴다(CLAUDE.md §3).
명세에 없어 새로 만든 코드는 ASSUMPTION 주석을 달았다.
"""

from typing import Any


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status = 500
    message = "Internal error"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or type(self).message
        self.details = details
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def _err(code: str, status: int, message: str) -> type[AppError]:
    return type(code, (AppError,), {"code": code, "status": status, "message": message})


# --- 공통 ---------------------------------------------------------------
# ASSUMPTION: 공통 에러 코드는 명세 13종에 개별 명시가 없어 아래 이름으로 고정한다.
ValidationError = _err("VALIDATION_ERROR", 422, "요청 값이 올바르지 않습니다")
Unauthorized = _err("UNAUTHORIZED", 401, "인증이 필요합니다")
Forbidden = _err("FORBIDDEN", 403, "권한이 없습니다")

# --- AUTH ---------------------------------------------------------------
EmailAlreadyExists = _err("EMAIL_ALREADY_EXISTS", 409, "이미 사용 중인 이메일입니다")
InvalidCredentials = _err("INVALID_CREDENTIALS", 401, "이메일 또는 비밀번호가 올바르지 않습니다")
InvalidRefreshToken = _err("INVALID_REFRESH_TOKEN", 401, "리프레시 토큰이 유효하지 않습니다")
UserNotFound = _err("USER_NOT_FOUND", 404, "사용자를 찾을 수 없습니다")

# --- TEAM ---------------------------------------------------------------
TeamNotFound = _err("TEAM_NOT_FOUND", 404, "팀을 찾을 수 없습니다")
TeamHasActiveProjects = _err("TEAM_HAS_ACTIVE_PROJECTS", 409, "팀에 프로젝트가 남아 있습니다")
AlreadyMember = _err("ALREADY_MEMBER", 409, "이미 멤버입니다")
DuplicateInvitation = _err("DUPLICATE_INVITATION", 409, "이미 대기 중인 초대가 있습니다")
InvitationNotFound = _err("INVITATION_NOT_FOUND", 404, "초대를 찾을 수 없습니다")
InvitationNotPending = _err("INVITATION_NOT_PENDING", 409, "이미 처리된 초대입니다")
InvitationExpired = _err("INVITATION_EXPIRED", 410, "초대가 만료되었습니다")
InvitationEmailMismatch = _err("INVITATION_EMAIL_MISMATCH", 403, "초대 대상 계정이 아닙니다")
MemberNotFound = _err("MEMBER_NOT_FOUND", 404, "멤버를 찾을 수 없습니다")
LastOwnerCannotLeave = _err("LAST_OWNER_CANNOT_LEAVE", 409, "마지막 소유자는 나갈 수 없습니다")

# --- PRJ ----------------------------------------------------------------
ProjectNotFound = _err("PROJECT_NOT_FOUND", 404, "프로젝트를 찾을 수 없습니다")
InvalidOwnerType = _err("INVALID_OWNER_TYPE", 400, "owner_type 과 team_id 조합이 올바르지 않습니다")

# --- 잡 파이프라인 (Phase 2 에서 사용) ----------------------------------
InvalidStatusTransition = _err("INVALID_STATUS_TRANSITION", 409, "허용되지 않는 상태 전이입니다")
