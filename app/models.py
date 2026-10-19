"""SQLAlchemy models.

Every relationship uses lazy="raise": an accidental lazy load raises instead of
silently firing an extra query (N+1). Load related rows explicitly with
selectinload()/joinedload(), or select the columns you need.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("buddy_mode IN ('on', 'quiet', 'off')", name="ck_profiles_buddy_mode"),
        CheckConstraint("daily_goal_minutes BETWEEN 5 AND 720", name="ck_profiles_daily_goal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # = Supabase auth user id
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(60))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Mascot "Dot": on | quiet (no speech bubbles) | off
    buddy_mode: Mapped[str] = mapped_column(String(8), nullable=False, server_default="on")
    daily_goal_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    last_reminder_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created_at()


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        Index("ix_topics_user_id", "user_id"),
        Index("uq_topics_user_name", "user_id", text("lower(name)"), unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        Index("ix_study_sessions_user_started", "user_id", text("started_at DESC")),
        CheckConstraint("ended_at > started_at", name="ck_sessions_order"),
        CheckConstraint("focus_minutes BETWEEN 1 AND 720", name="ck_sessions_minutes"),
    )

    # Client-generated id makes offline sync idempotent (retries can't duplicate).
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    focus_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    distractions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    label: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = _created_at()

    topic: Mapped[Topic | None] = relationship(lazy="raise")


class DailySummary(Base):
    """One row per user per local day. Upserted when a session is saved, so the
    heatmap is a single indexed read of at most 365 rows."""

    __tablename__ = "daily_summary"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_summary_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    session_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    minutes_by_topic: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_user_created", "user_id", text("created_at DESC")),
        UniqueConstraint("user_id", "content_hash", name="uq_notes_user_hash"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_points: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_text: Mapped[str | None] = mapped_column(Text)  # extracted text only; uploads are never stored
    source_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # image | pdf | text
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    topic: Mapped[Topic | None] = relationship(lazy="raise")
    flashcards: Mapped[list["Flashcard"]] = relationship(back_populates="note", lazy="raise")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="note", lazy="raise")


class Flashcard(Base):
    __tablename__ = "flashcards"
    __table_args__ = (
        Index("ix_flashcards_user_due", "user_id", "due_at"),
        Index("ix_flashcards_user_created", "user_id", text("created_at DESC")),
        Index("ix_flashcards_note_id", "note_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL")
    )
    note_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE")
    )
    front: Mapped[str] = mapped_column(String(300), nullable=False)
    back: Mapped[str] = mapped_column(String(1000), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ai")  # ai | wrong_answer
    stage: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    reviews: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    topic: Mapped[Topic | None] = relationship(lazy="raise")
    note: Mapped[Note | None] = relationship(back_populates="flashcards", lazy="raise")


class Quiz(Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        Index("ix_quizzes_user_created", "user_id", text("created_at DESC")),
        Index("ix_quizzes_cache_key", "cache_key"),
        UniqueConstraint("user_id", "cache_key", name="uq_quizzes_user_cache_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL")
    )
    note_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    questions: Mapped[list] = mapped_column(JSONB, nullable=False)
    cache_key: Mapped[str] = mapped_column(String(160), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    best_score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()

    topic: Mapped[Topic | None] = relationship(lazy="raise")
    note: Mapped[Note | None] = relationship(back_populates="quizzes", lazy="raise")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (Index("ix_quiz_attempts_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quizzes.id", ondelete="CASCADE")
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class CoachReport(Base):
    __tablename__ = "coach_reports"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_coach_reports_user_week"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AiUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_ai_usage_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    date: Mapped[date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class AiCache(Base):
    """Generic cache for small AI results (e.g. command-bar parses)."""

    __tablename__ = "ai_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of kind + input
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
