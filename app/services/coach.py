"""AI Weekly Coach.

Generated lazily: the first dashboard visit after a week (Mon-Sun, user's
timezone) ends reports status "pending"; the client then calls
POST /api/coach/generate. Reports are cached forever (unique per user+week),
and weeks with no activity never call the AI.
"""

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoachReport
from app.services.profiles import zone


def week_bounds(today: date) -> tuple[date, date]:
    """(start of last finished week, start of current week); weeks start Monday."""
    this_week = today - timedelta(days=today.weekday())
    return this_week - timedelta(days=7), this_week


_WEEK_STATS_SQL = text(
    """
    WITH week AS (
      SELECT date, total_minutes, session_count, minutes_by_topic FROM daily_summary
      WHERE user_id = :uid AND date >= :ws AND date < :we
    ),
    by_topic AS (
      SELECT kv.key AS topic_key, SUM(kv.value::int) AS minutes
      FROM week, jsonb_each_text(week.minutes_by_topic) AS kv
      GROUP BY kv.key
    ),
    attempts AS (
      SELECT score, total FROM quiz_attempts
      WHERE user_id = :uid AND created_at >= :ws_ts AND created_at < :we_ts
    )
    SELECT
      (SELECT COALESCE(SUM(total_minutes), 0) FROM week) AS total_minutes,
      (SELECT COALESCE(SUM(session_count), 0) FROM week) AS sessions,
      (SELECT COUNT(*) FROM week WHERE total_minutes >= 15) AS present_days,
      (SELECT COALESCE(json_agg(json_build_object('date', date, 'minutes', total_minutes)
                                ORDER BY date), '[]')
         FROM week) AS days,
      (SELECT COALESCE(json_agg(json_build_object('topic', COALESCE(t.name, 'No topic'), 'minutes', b.minutes)
                                ORDER BY b.minutes DESC), '[]')
         FROM by_topic b LEFT JOIN topics t ON t.id::text = b.topic_key AND t.user_id = :uid) AS topics,
      (SELECT COALESCE(SUM(total_minutes), 0) FROM daily_summary
         WHERE user_id = :uid AND date >= :pws AND date < :ws) AS prev_week_minutes,
      (SELECT COUNT(*) FROM attempts) AS quizzes_taken,
      (SELECT ROUND(100.0 * SUM(score) / NULLIF(SUM(total), 0)) FROM attempts) AS quiz_accuracy_pct,
      (SELECT ROUND(AVG(distractions)::numeric, 1) FROM study_sessions
         WHERE user_id = :uid AND started_at >= :ws_ts AND started_at < :we_ts) AS avg_distractions
    """
).bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))


async def week_stats(db: AsyncSession, user_id: uuid.UUID, tz: str, week_start: date) -> dict:
    tzinfo = zone(tz)
    week_end = week_start + timedelta(days=7)
    row = (
        (
            await db.execute(
                _WEEK_STATS_SQL,
                {
                    "uid": user_id,
                    "ws": week_start,
                    "we": week_end,
                    "pws": week_start - timedelta(days=7),
                    "ws_ts": datetime.combine(week_start, time(), tzinfo),
                    "we_ts": datetime.combine(week_end, time(), tzinfo),
                },
            )
        )
        .mappings()
        .one()
    )
    stats = dict(row)
    for key in ("quiz_accuracy_pct", "avg_distractions"):
        if stats[key] is not None:
            stats[key] = float(stats[key])
    stats["week_start"] = week_start.isoformat()
    return stats


async def cached_report(db: AsyncSession, user_id: uuid.UUID, week_start: date) -> CoachReport | None:
    return await db.scalar(
        select(CoachReport).where(CoachReport.user_id == user_id, CoachReport.week_start == week_start)
    )


async def save_report(
    db: AsyncSession, user_id: uuid.UUID, week_start: date, summary: str, report: dict
) -> CoachReport:
    saved = await db.scalar(
        insert(CoachReport)
        .values(user_id=user_id, week_start=week_start, summary=summary, report=report)
        .on_conflict_do_nothing(constraint="uq_coach_reports_user_week")
        .returning(CoachReport)
    )
    return saved or await cached_report(db, user_id, week_start)
