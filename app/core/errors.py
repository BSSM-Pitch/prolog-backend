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
# 명세의 공통 에러 코드는 INVALID_INPUT(400) 이다. VALIDATION_ERROR 는 존재하지 않는다.
InvalidInput = _err("INVALID_INPUT", 400, "요청 값이 올바르지 않습니다")
Unauthorized = _err("UNAUTHORIZED", 401, "인증이 필요합니다")
# AUTH 에는 FORBIDDEN 이 없다. TEAM·PRJ 공통 코드다.
Forbidden = _err("FORBIDDEN", 403, "권한이 없습니다")

# --- AUTH (v0.2, Google 단일) -------------------------------------------
UsernameRequired = _err("USERNAME_REQUIRED", 400, "아이디가 필요합니다")
UsernameTaken = _err("USERNAME_TAKEN", 409, "이미 사용 중인 아이디입니다")
SignupTicketInvalid = _err(
    "SIGNUP_TICKET_INVALID", 401, "가입 티켓이 만료되었거나 올바르지 않습니다"
)
RefreshTokenInvalid = _err("REFRESH_TOKEN_INVALID", 401, "리프레시 토큰이 유효하지 않습니다")
OAuthProviderError = _err(
    "OAUTH_PROVIDER_ERROR", 502, "소셜 로그인 공급자 응답이 올바르지 않습니다"
)
UserNotFound = _err("USER_NOT_FOUND", 404, "사용자를 찾을 수 없습니다")

# --- TEAM ---------------------------------------------------------------
# 멤버·초대 관련 코드는 PRJ 와 이름이 다르다. 공통 처리로 뭉개지 않는다 (CLAUDE.md §5.1).
TeamNotFound = _err("TEAM_NOT_FOUND", 404, "팀을 찾을 수 없습니다")
TeamHasActiveProjects = _err("TEAM_HAS_ACTIVE_PROJECTS", 409, "팀에 프로젝트가 남아 있습니다")
AlreadyTeamMember = _err("ALREADY_TEAM_MEMBER", 409, "이미 팀 멤버입니다")
TeamInvitationNotFound = _err("TEAM_INVITATION_NOT_FOUND", 404, "초대를 찾을 수 없습니다")
TeamMemberNotFound = _err("TEAM_MEMBER_NOT_FOUND", 404, "팀 멤버를 찾을 수 없습니다")

# --- PRJ ----------------------------------------------------------------
ProjectNotFound = _err("PROJECT_NOT_FOUND", 404, "프로젝트를 찾을 수 없습니다")
InvalidOwnerType = _err("INVALID_OWNER_TYPE", 400, "owner_type 과 team_id 조합이 올바르지 않습니다")
AlreadyMember = _err("ALREADY_MEMBER", 409, "이미 멤버입니다")
InvitationNotFound = _err("INVITATION_NOT_FOUND", 404, "초대를 찾을 수 없습니다")
MemberNotFound = _err("MEMBER_NOT_FOUND", 404, "멤버를 찾을 수 없습니다")

# --- TEAM · PRJ 공통 ----------------------------------------------------
DuplicateInvitation = _err("DUPLICATE_INVITATION", 409, "이미 대기 중인 초대가 있습니다")
InvitationNotPending = _err("INVITATION_NOT_PENDING", 409, "이미 처리된 초대입니다")
InvitationExpired = _err("INVITATION_EXPIRED", 410, "초대가 만료되었습니다")
InvitationEmailMismatch = _err("INVITATION_EMAIL_MISMATCH", 403, "초대 대상 계정이 아닙니다")
LastOwnerCannotLeave = _err("LAST_OWNER_CANNOT_LEAVE", 409, "마지막 소유자는 나갈 수 없습니다")

# --- 잡 파이프라인 (Phase 2 에서 사용) ----------------------------------
InvalidStatusTransition = _err("INVALID_STATUS_TRANSITION", 409, "허용되지 않는 상태 전이입니다")
