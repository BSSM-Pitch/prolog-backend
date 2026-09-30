"""도메인 예외 → 에러코드 · HTTP 상태 매핑.

에러 코드 문자열은 명세에 적힌 값을 그대로 쓴다(CLAUDE.md §3).
명세에 없어 새로 만든 코드는 ASSUMPTION 주석을 달았다.
"""

from typing import Any

from sqlalchemy.exc import IntegrityError


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
# 팀 프로젝트를 만들려는 비소속자. 일반 FORBIDDEN 이 아니다 (CLAUDE.md §6.1·§6.2).
NotTeamMember = _err("NOT_TEAM_MEMBER", 403, "해당 팀의 멤버가 아닙니다")
AlreadyMember = _err("ALREADY_MEMBER", 409, "이미 멤버입니다")
InvitationNotFound = _err("INVITATION_NOT_FOUND", 404, "초대를 찾을 수 없습니다")
MemberNotFound = _err("MEMBER_NOT_FOUND", 404, "멤버를 찾을 수 없습니다")

# --- TEAM · PRJ 공통 ----------------------------------------------------
DuplicateInvitation = _err("DUPLICATE_INVITATION", 409, "이미 대기 중인 초대가 있습니다")
InvitationNotPending = _err("INVITATION_NOT_PENDING", 409, "이미 처리된 초대입니다")
InvitationExpired = _err("INVITATION_EXPIRED", 410, "초대가 만료되었습니다")
LastOwnerCannotLeave = _err("LAST_OWNER_CANNOT_LEAVE", 409, "마지막 소유자는 나갈 수 없습니다")

# --- MSU ----------------------------------------------------------------
ManuscriptNotFound = _err("MANUSCRIPT_NOT_FOUND", 404, "원고를 찾을 수 없습니다")
ChapterNotFound = _err("CHAPTER_NOT_FOUND", 404, "챕터를 찾을 수 없습니다")
UnsupportedFileFormat = _err("UNSUPPORTED_FILE_FORMAT", 400, "지원하지 않는 파일 형식입니다")
SourceTypeImmutable = _err("SOURCE_TYPE_IMMUTABLE", 409, "source_type 은 생성 후 바꿀 수 없습니다")

# --- NOTI ---------------------------------------------------------------
NotificationNotFound = _err("NOTIFICATION_NOT_FOUND", 404, "알림을 찾을 수 없습니다")

# --- ASS (§1.4) ---------------------------------------------------------
DraftNotFound = _err("DRAFT_NOT_FOUND", 404, "해당 초안을 찾을 수 없습니다")
ItemNotFound = _err("ITEM_NOT_FOUND", 404, "초안 항목을 찾을 수 없습니다")
CharacterNotFound = _err("CHARACTER_NOT_FOUND", 404, "캐릭터를 찾을 수 없습니다")
DraftAlreadyResolved = _err("DRAFT_ALREADY_RESOLVED", 409, "이미 확정되었거나 폐기된 초안입니다")
MissingRequiredField = _err("MISSING_REQUIRED_FIELD", 400, "확정에 필요한 항목이 비어 있습니다")
DuplicateCharacterCandidate = _err(
    "DUPLICATE_CHARACTER_CANDIDATE", 409, "같은 이름의 확정된 캐릭터가 이미 있습니다"
)

# --- NLCD (§1.4) --------------------------------------------------------
ExtractionNotFound = _err("EXTRACTION_NOT_FOUND", 404, "해당 추출 작업을 찾을 수 없습니다")
TargetCharacterNotFound = _err(
    "TARGET_CHARACTER_NOT_FOUND", 404, "target_character_id 로 지정한 캐릭터가 없습니다"
)
ExtractionNotReady = _err("EXTRACTION_NOT_READY", 409, "추출이 아직 완료되지 않았습니다")
AlreadyForwarded = _err("ALREADY_FORWARDED", 409, "이미 구조화 시스템으로 전달된 추출 결과입니다")

# --- REX (§1.4) ---------------------------------------------------------
WorldRuleNotFound = _err("WORLD_RULE_NOT_FOUND", 404, "규칙을 찾을 수 없습니다")
RuleExtractionNotFound = _err("RULE_EXTRACTION_NOT_FOUND", 404, "추출 작업을 찾을 수 없습니다")
# EXTRACTION_NOT_READY(409) 는 NLCD 와 같은 코드다 — ExtractionNotReady 를 함께 쓴다.

# --- FTS (§1.4) ---------------------------------------------------------
ForeshadowingNotFound = _err("FORESHADOWING_NOT_FOUND", 404, "해당 복선을 찾을 수 없습니다")
LinkedChapterNotFound = _err("LINKED_CHAPTER_NOT_FOUND", 404, "연결 챕터가 없습니다")
LinkTargetNotFound = _err("LINK_TARGET_NOT_FOUND", 404, "연결하려는 대상이 없습니다")
InvalidPayoffChapter = _err(
    "INVALID_PAYOFF_CHAPTER", 400, "회수 챕터는 설치 챕터보다 앞설 수 없습니다"
)
PayoffNotSet = _err("PAYOFF_NOT_SET", 409, "회수되지 않은 복선입니다")

# --- 잡 파이프라인 (Phase 2 에서 사용) ----------------------------------
InvalidStatusTransition = _err("INVALID_STATUS_TRANSITION", 409, "허용되지 않는 상태 전이입니다")


def constraint_name(exc: IntegrityError) -> str | None:
    """IntegrityError 를 일으킨 제약의 **이름**.

    메시지 문자열 파싱은 PG 마이너 버전에 깨진다(CLAUDE.md §7). 드라이버가 예외 체인
    어딘가에 붙여 주는 ``constraint_name`` 을 찾아 올린다.
    """
    err: BaseException | None = exc.orig
    while err is not None:
        name = getattr(err, "constraint_name", None)
        if name:
            return str(name)
        err = err.__cause__
    return None
