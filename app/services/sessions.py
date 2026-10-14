"""Saving a focus session: the single DB write per timer run.

Three round trips: (1) validate (timezone, topic ownership, overlap) in one
statement, (2) idempotent insert, (3) daily_summary upsert.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.errors import AppError
from app.models import StudySession
from app.schemas import SessionCreate
from app.services.profiles import zone

CLOCK_SKEW = timedelta(minutes=2)
NO_TOPIC_KEY = "none"

_VALIDATE_SQL = text(
    """
    SELECT p.timezone,
      EXISTS (
        SELECT 1 FROM study_sessions s
        WHERE s.user_id = :uid AND s.id <> :sid
          AND s.started_at < :ended_at AND s.ended_at > :started_at
      ) AS overlaps,
      (CAST(:topic_id AS uuid) IS NULL OR EXISTS (
        SELECT 1 FROM topics t WHERE t.id = :topic_id AND t.user_id = :uid
      )) AS topic_ok
    FROM profiles p WHERE p.id = :uid
    """
).bindparams(
    bindparam("uid", type_=PG_UUID(as_uuid=True)),
    bindparam("sid", type_=PG_UUID(as_uuid=True)),
    bindparam("topic_id", type_=PG_UUID(as_uuid=True)),
)

_UPSERT_SUMMARY_SQL = text(
    """
    INSERT INTO daily_summary (user_id, date, total_minutes, session_count, minutes_by_topic)
    VALUES (:uid, :day, CAST(:minutes AS int), 1,
            jsonb_build_object(CAST(:tkey AS text), CAST(:minutes AS int)))
    ON CONFLICT (user_id, date) DO UPDATE SET
      total_minutes = daily_summary.total_minutes + EXCLUDED.total_minutes,
      session_count = daily_summary.session_count + 1,
      minutes_by_topic = daily_summary.minutes_by_topic || jsonb_build_object(
        CAST(:tkey AS text),
        COALESCE((daily_summary.minutes_by_topic ->> CAST(:tkey AS text))::int, 0) + CAST(:minutes AS int)
      )
    RETURNING total_minutes
    """
).bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))


async def save_session(db: AsyncSession, user: CurrentUser, data: SessionCreate) -> dict:
    now = datetime.now(UTC)
    if data.ended_at > now + CLOCK_SKEW or data.started_at > now + CLOCK_SKEW:
        raise AppError(422, "future_session", "Session times can't be in the future")

    check = (
        await db.execute(
            _VALIDATE_SQL,
            {
                "uid": user.id,
                "sid": data.id,
                "topic_id": data.topic_id,
                "started_at": data.started_at,
                "ended_at": data.ended_at,
            },
        )
    ).one_or_none()
    if check is None:
        raise AppError(409, "profile_missing", "Profile not initialised; call GET /api/me first")
    tz, overlaps, topic_ok = check
    if not topic_ok:
        raise AppError(404, "not_found", "Topic not found")
    if overlaps and not user.is_demo:  # many visitors share the demo account's clock
        raise AppError(409, "session_overlap", "This session overlaps one you already saved")

    inserted = await db.scalar(
        insert(StudySession)
        .values(user_id=user.id, **data.model_dump())
        .on_conflict_do_nothing(index_elements=[StudySession.id])
        .returning(StudySession)
    )
    day = data.started_at.astimezone(zone(tz)).date()

    if inserted is None:
        # Same id already saved (offline retry). Only return it if it's ours.
        existing = await db.scalar(
            select(StudySession).where(StudySession.id == data.id, StudySession.user_id == user.id)
        )
        if existing is None:
            raise AppError(409, "conflict", "Session id already in use")
        day_minutes = await db.scalar(
            text("SELECT total_minutes FROM daily_summary WHERE user_id = :uid AND date = :day").bindparams(
                bindparam("uid", type_=PG_UUID(as_uuid=True))
            ),
            {"uid": user.id, "day": day},
        )
        return {
            "session": existing,
            "created": False,
            "date": day,
            "day_minutes": day_minutes or 0,
        }

    day_minutes = await db.scalar(
        _UPSERT_SUMMARY_SQL,
        {
            "uid": user.id,
            "day": day,
            "minutes": data.focus_minutes,
            "tkey": str(data.topic_id) if data.topic_id else NO_TOPIC_KEY,
        },
    )
    await db.commit()
    return {
        "session": inserted,
        "created": True,
        "date": day,
        "day_minutes": day_minutes,
    }
