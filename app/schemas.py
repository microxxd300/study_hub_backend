import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------- base classes


class In(BaseModel):
    """Request bodies: unknown fields are rejected, strings are trimmed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


BuddyMode = Literal["on", "quiet", "off"]
TopicColor = Literal["snow", "ash", "lime", "mint", "sky", "iris", "rose", "amber"]
Name40 = Annotated[str, StringConstraints(min_length=1, max_length=40)]


def _valid_tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Unknown timezone") from exc
    return v


Timezone = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(_valid_tz)]


def _aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("Timestamp must include a timezone offset")
    return v


AwareDatetime = Annotated[datetime, AfterValidator(_aware)]

# ---------------------------------------------------------------- profile


class ProfileOut(Out):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    timezone: str
    reminders_enabled: bool
    onboarded: bool
    buddy_mode: BuddyMode = "on"
    daily_goal_minutes: int = 60
    is_demo: bool = False


class ProfileUpdate(In):
    display_name: Annotated[str, StringConstraints(max_length=60)] | None = None
    timezone: Timezone | None = None
    reminders_enabled: bool | None = None
    onboarded: bool | None = None
    buddy_mode: BuddyMode | None = None
    daily_goal_minutes: int | None = Field(default=None, ge=5, le=720)


# ---------------------------------------------------------------- topics


class TopicCreate(In):
    name: Name40
    color: TopicColor


class TopicUpdate(In):
    name: Name40 | None = None
    color: TopicColor | None = None
    archived: bool | None = None


class TopicOut(Out):
    id: uuid.UUID
    name: str
    color: str
    archived: bool
    created_at: datetime


# ---------------------------------------------------------------- sessions


class SessionCreate(In):
    id: uuid.UUID  # generated on the client -> idempotent offline sync
    topic_id: uuid.UUID | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime
    focus_minutes: int = Field(ge=1, le=720)
    distractions: int = Field(default=0, ge=0, le=999)
    label: Annotated[str, StringConstraints(max_length=80)] | None = None

    @model_validator(mode="after")
    def _check_times(self) -> "SessionCreate":
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        span = (self.ended_at - self.started_at).total_seconds() / 60
        if span > 12 * 60:
            raise ValueError("A session can be at most 12 hours")
        if self.focus_minutes > span + 1:
            raise ValueError("focus_minutes can't exceed the session length")
        return self


class SessionOut(Out):
    id: uuid.UUID
    topic_id: uuid.UUID | None
    started_at: datetime
    ended_at: datetime
    focus_minutes: int
    distractions: int
    label: str | None


class SessionSaved(BaseModel):
    session: SessionOut
    created: bool  # false when this id was already saved (offline retry)
    date: date  # local date the minutes were credited to
    day_minutes: int


# ---------------------------------------------------------------- dashboard / heatmap


class HeatmapDay(BaseModel):
    d: date
    m: int  # minutes
    t: dict[str, int] | None = None  # minutes by topic id


class StreakStats(BaseModel):
    current_streak: int
    longest_streak: int
    total_minutes: int
    present_days: int


class HeatmapOut(StreakStats):
    days: list[HeatmapDay]
    today: date
    topic_id: uuid.UUID | None = None


class CoachReportOut(Out):
    week_start: date
    summary: str
    report: dict
    created_at: datetime


class CoachState(BaseModel):
    # ready: report for the latest finished week exists; pending: client should
    # call POST /api/coach/generate; no_activity: nothing to coach; none: new user
    status: Literal["ready", "pending", "no_activity", "none"]
    week_start: date | None = None
    report: CoachReportOut | None = None


class DashboardOut(StreakStats):
    profile: ProfileOut
    today: date
    today_minutes: int
    due_cards: int
    topics: list[TopicOut]
    heatmap: list[HeatmapDay]
    coach: CoachState


# ---------------------------------------------------------------- notes / flashcards / quizzes


class NoteListItem(Out):
    id: uuid.UUID
    title: str
    topic_id: uuid.UUID | None
    source_kind: str
    created_at: datetime


class FlashcardOut(Out):
    id: uuid.UUID
    front: str
    back: str
    topic_id: uuid.UUID | None
    note_id: uuid.UUID | None
    origin: str
    stage: int
    due_at: datetime


class QuizListItem(Out):
    id: uuid.UUID
    title: str
    topic_id: uuid.UUID | None
    note_id: uuid.UUID | None
    attempts: int
    best_score: int | None
    created_at: datetime


class NoteDetail(Out):
    id: uuid.UUID
    title: str
    topic_id: uuid.UUID | None
    source_kind: str
    summary: str
    key_points: list[str]
    source_text: str | None
    created_at: datetime
    flashcards: list[FlashcardOut]
    quizzes: list[QuizListItem]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str = ""


class QuizDetail(QuizListItem):
    questions: list[QuizQuestion]


class FlashcardReview(In):
    correct: bool


class QuizGenerate(In):
    note_id: uuid.UUID | None = None
    topic_name: Annotated[str, StringConstraints(min_length=2, max_length=80)] | None = None
    topic_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "QuizGenerate":
        if bool(self.note_id) == bool(self.topic_name):
            raise ValueError("Provide exactly one of note_id or topic_name")
        return self


class QuizSubmit(In):
    answers: list[int] = Field(min_length=1, max_length=20)

    @field_validator("answers")
    @classmethod
    def _range(cls, v: list[int]) -> list[int]:
        if any(a < -1 or a > 3 for a in v):  # -1 = skipped
            raise ValueError("Answer index out of range")
        return v


class QuizResultOut(BaseModel):
    score: int
    total: int
    new_flashcards: int
    best_score: int


# ---------------------------------------------------------------- command bar


class CommandParseIn(In):
    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class CommandParseOut(BaseModel):
    minutes: int
    topic_id: uuid.UUID | None
    topic_name: str | None
    label: str | None
    source: Literal["regex", "ai"]


# ---------------------------------------------------------------- AI output schemas
# Structure is strict (wrong shape -> rejected); over-long strings are trimmed
# rather than failing a whole generation.


def _clip(n: int):
    def inner(v: str) -> str:
        v = " ".join(v.split()) if n <= 400 else v.strip()
        return v[:n]

    return AfterValidator(inner)


def _text(n: int):
    return Annotated[str, StringConstraints(min_length=1), _clip(n)]


class AiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AiFlashcard(AiModel):
    front: _text(300)
    back: _text(600)


class AiQuizQuestion(AiModel):
    question: _text(300)
    options: list[_text(150)] = Field(min_length=4, max_length=4)
    answer_index: int = Field(ge=0, le=3)
    explanation: Annotated[str, _clip(400)] = ""


class AiSnapResult(AiModel):
    title: _text(120)
    summary: _text(1500)
    key_points: list[_text(240)] = Field(min_length=3, max_length=10)
    flashcards: list[AiFlashcard] = Field(min_length=5, max_length=10)
    quiz: list[AiQuizQuestion] = Field(min_length=5, max_length=5)
    extracted_text: Annotated[str, _clip(6000)] = ""


class AiQuizResult(AiModel):
    title: _text(120)
    questions: list[AiQuizQuestion] = Field(min_length=5, max_length=5)


class AiCoachResult(AiModel):
    headline: _text(120)
    summary: _text(280)
    wins: list[_text(200)] = Field(max_length=3)
    focus_areas: list[_text(200)] = Field(max_length=3)
    plan: list[_text(200)] = Field(min_length=1, max_length=5)


class AiCommandResult(AiModel):
    minutes: int | None = Field(default=None, ge=1, le=720)
    topic: Annotated[str, _clip(40)] | None = None
    label: Annotated[str, _clip(80)] | None = None


# ---------------------------------------------------------------- weekly stats


class WeekStat(BaseModel):
    week_start: date  # Monday, user's timezone
    total_minutes: int
    present_days: int
    by_topic: dict[str, int]  # topic id (or "none") -> minutes


class TopicTotal(BaseModel):
    topic_id: str  # topic id, or "none"
    minutes: int


class WeeklyStatsOut(BaseModel):
    weeks: list[WeekStat]  # oldest first, every week present (zeros filled)
    topics: list[TopicTotal]  # whole period, most minutes first
