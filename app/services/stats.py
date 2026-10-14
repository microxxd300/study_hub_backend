"""Streaks, totals and heatmap — all aggregated in SQL over daily_summary.

A day counts as "present" at >= 15 minutes. Streaks use the classic
gaps-and-islands trick: consecutive dates minus their row number are constant,
so GROUP BY that value yields each run of consecutive days.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

PRESENT_MINUTES = 15
HEATMAP_DAYS = 365


def _minutes_expr(topic_id: uuid.UUID | None) -> str:
    # Topic filter reads one key out of the minutes_by_topic jsonb.
    if topic_id is None:
        return "total_minutes"
    return "COALESCE((minutes_by_topic ->> :topic_key)::int, 0)"


def _params(user_id: uuid.UUID, topic_id: uuid.UUID | None, **extra) -> dict:
    params = {"uid": user_id, **extra}
    if topic_id is not None:
        params["topic_key"] = str(topic_id)
    return params


_STATS_SQL = """
WITH present AS (
    SELECT date FROM daily_summary
    WHERE user_id = :uid AND {m} >= :present
),
grouped AS (
    SELECT date, date - (ROW_NUMBER() OVER (ORDER BY date))::int AS grp FROM present
),
islands AS (
    SELECT MAX(date) AS end_date, COUNT(*) AS len FROM grouped GROUP BY grp
)
SELECT
    COALESCE((SELECT MAX(len) FROM islands), 0) AS longest_streak,
    -- a streak is still alive if it ended today or yesterday
    COALESCE((SELECT MAX(len) FROM islands WHERE end_date >= CAST(:today AS date) - 1), 0) AS current_streak,
    (SELECT COUNT(*) FROM present) AS present_days,
    (SELECT COALESCE(SUM({m}), 0) FROM daily_summary WHERE user_id = :uid) AS total_minutes
    {extra}
"""


async def streak_stats(
    db: AsyncSession,
    user_id: uuid.UUID,
    today: date,
    topic_id: uuid.UUID | None = None,
    *,
    with_dashboard_extras: bool = False,
    last_week: tuple[date, date] | None = None,
) -> dict:
    """One round trip. With dashboard extras it also returns the due-card count and
    last week's minutes (used to decide whether a coach report is needed)."""
    extra = ""
    extra_params: dict = {}
    if with_dashboard_extras:
        extra = """,
    (SELECT COUNT(*) FROM flashcards WHERE user_id = :uid AND due_at <= now()) AS due_cards,
    (SELECT COALESCE(SUM(total_minutes), 0) FROM daily_summary
        WHERE user_id = :uid AND date >= :lw_start AND date < :lw_end) AS last_week_minutes"""
        assert last_week is not None
        extra_params = {"lw_start": last_week[0], "lw_end": last_week[1]}

    sql = text(_STATS_SQL.format(m=_minutes_expr(topic_id), extra=extra)).bindparams(
        bindparam("uid", type_=PG_UUID(as_uuid=True))
    )
    row = (
        (
            await db.execute(
                sql,
                _params(
                    user_id,
                    topic_id,
                    present=PRESENT_MINUTES,
                    today=today,
                    **extra_params,
                ),
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def heatmap_days(
    db: AsyncSession, user_id: uuid.UUID, today: date, topic_id: uuid.UUID | None = None
) -> list[dict]:
    """At most 365 rows straight off the (user_id, date) unique index."""
    start = today - timedelta(days=HEATMAP_DAYS - 1)
    if topic_id is None:
        sql = text(
            "SELECT date AS d, total_minutes AS m, minutes_by_topic AS t FROM daily_summary "
            "WHERE user_id = :uid AND date >= :start AND date <= :today AND total_minutes > 0 ORDER BY date"
        )
    else:
        sql = text(
            f"SELECT date AS d, {_minutes_expr(topic_id)} AS m FROM daily_summary "
            f"WHERE user_id = :uid AND date >= :start AND date <= :today "
            f"AND (minutes_by_topic ->> :topic_key) IS NOT NULL ORDER BY date"
        )
    sql = sql.bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))
    rows = await db.execute(sql, _params(user_id, topic_id, start=start, today=today))
    return [dict(r) for r in rows.mappings()]
