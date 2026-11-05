from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from prolog_ai.memory.schema import Evidence, Hypothesis, Observation, Pattern
from pydantic import BaseModel, Field

from app.ai.character_assessment import AxisAssessment, AxisId


class Progress(BaseModel):
    processed_scenes: int
    total_scenes: int
    observations: int
    verified_speakers: int
    patterns: int
    completed_requests: int
    stage: str


class AnalysisError(BaseModel):
    code: str | None
    message: str


class DiscoveredCharacter(BaseModel):
    candidate_id: str
    name: str
    kind: Literal["named_person", "ambiguous"]
    alias_of: str | None
    reason: str
    evidence: Evidence
    status: Literal["pending", "added", "existing", "needs_review", "confirmed", "dismissed"]
    character_id: str | None


class CharacterDiscoveryProgress(BaseModel):
    version: int
    processed_chunks: int
    total_chunks: int
    complete: bool
    candidates: list[DiscoveredCharacter]


class ResolveDiscoveredCharacter(BaseModel):
    action: Literal["create", "link", "dismiss"]
    character_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)


class CharacterMemoryAnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued", "running", "completed", "failed", "skipped"]
    created_at: datetime
    finished_at: datetime | None
    progress: Progress
    error: AnalysisError | None
    discovery: CharacterDiscoveryProgress | None = None


class ChapterOption(BaseModel):
    order: int
    title: str


class OverviewResponse(BaseModel):
    manuscript_id: str
    job: CharacterMemoryAnalysisResponse | None
    available_analysis_id: str | None
    stale: bool
    job_stale: bool
    mode: Literal["real", "fake"] | None
    character_counts: dict[str, int]
    chapters: list[ChapterOption]
    can_analyze: bool


class SourceContext(BaseModel):
    before: str
    quote: str
    after: str


class SpeakerCheck(BaseModel):
    reason: str
    adjudicated: bool
    evidence: Evidence


class DetailRecord(Observation):
    order: int
    chapter_id: str | None
    scene_title: str
    story_time: str | None
    interlocutors: list[str]
    source: SourceContext
    speaker_check: SpeakerCheck | None


class MetricPeriod(BaseModel):
    orders: list[int]
    record_count: int
    record_ids: list[str]
    choice_count: int
    reaction_count: int
    choice_ids: list[str]
    reaction_ids: list[str]
    choice_share: float | None
    classified_count: int
    other_count: int
    small_sample: bool
    partner_counts: dict[str, int]
    interaction_ids: list[str]


class TimelinePoint(MetricPeriod):
    order: int


class Metrics(BaseModel):
    basis: Literal["extracted_records"]
    window_size: int
    recent: MetricPeriod
    previous: MetricPeriod
    choice_delta_pp: float | None
    timeline: list[TimelinePoint]


class Counts(BaseModel):
    observations: int
    patterns: int
    uncertain: int
    scenes: int
    speech: int
    action: int
    inner_thought: int
    narration: int


class Coverage(BaseModel):
    exhaustive: Literal[False]
    patterns_hidden_by_cutoff: int
    speaker_verification_complete: bool


class DetailsResponse(BaseModel):
    character_id: str
    character_name: str | None
    snapshot_id: str
    mode: Literal["real", "fake"]
    through_order: int | None
    counts: Counts
    observations: list[DetailRecord]
    metrics: Metrics
    uncertain_observations: list[DetailRecord]
    patterns: list[Pattern]
    hypotheses: list[Hypothesis]
    coverage: Coverage
    analysis_id: str
    generated_at: datetime
    stale: bool
    manuscript_id: str
    manuscript_title: str


class CharacterRubric(BaseModel):
    id: AxisId
    label: str
    group: str
    question: str
    anchors: list[str]
    caution: str


class CharacterAxisScore(AxisAssessment):
    rubric: CharacterRubric
    status: Literal["scored", "insufficient_evidence", "unverified_evidence"]
    score: float | None
    observed_range: list[int] | None
    rated_scenes: int


class CharacterAssessmentCoverage(BaseModel):
    available_records: int
    reviewed_records: int
    available_scenes: int
    reviewed_scenes: int
    sampling: str
    exhaustive: bool


class CharacterAssessmentResult(BaseModel):
    rubric_version: str
    axes: list[CharacterAxisScore]
    coverage: CharacterAssessmentCoverage
    minimum_rated_scenes: int
    aggregation: Literal["median_of_unique_scene_ratings"]
    meta: dict[str, Any]


class CharacterAssessmentJob(BaseModel):
    assessment_id: str
    status: Literal["queued", "running", "completed", "failed", "skipped"]
    created_at: datetime
    finished_at: datetime | None
    result: CharacterAssessmentResult | None
    error: str | None


class CharacterAssessmentOverview(BaseModel):
    rubric_version: str
    rubric: list[CharacterRubric]
    stale: bool
    memory_analysis_id: str
    job: CharacterAssessmentJob | None
