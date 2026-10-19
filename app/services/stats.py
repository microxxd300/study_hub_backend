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


# ---------------------------------------------------------------- weekly stats

_WEEKS_SQL = text(
    """
    WITH days AS (
      SELECT date, total_minutes, minutes_by_topic,
             (date - (EXTRACT(ISODOW FROM date)::int - 1))::date AS week_start
      FROM daily_summary
      WHERE user_id = :uid AND date >= :start AND date <= :today
    ),
    topic_week AS (
      SELECT d.week_start, kv.key AS topic, SUM(kv.value::int) AS minutes
      FROM days d, jsonb_each_text(d.minutes_by_topic) AS kv
      GROUP BY d.week_start, kv.key
    )
    SELECT w.week_start,
           SUM(w.total_minutes)::int AS total_minutes,
           COUNT(*) FILTER (WHERE w.total_minutes >= :present)::int AS present_days,
           COALESCE((SELECT jsonb_object_agg(t.topic, t.minutes) FROM topic_week t
                     WHERE t.week_start = w.week_start), '{}'::jsonb) AS by_topic
    FROM days w
    GROUP BY w.week_start
    ORDER BY w.week_start
    """
).bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))

_TOPIC_TOTALS_SQL = text(
    """
    SELECT kv.key AS topic_id, SUM(kv.value::int)::int AS minutes
    FROM daily_summary d, jsonb_each_text(d.minutes_by_topic) AS kv
    WHERE d.user_id = :uid AND d.date >= :start AND d.date <= :today
    GROUP BY kv.key
    ORDER BY minutes DESC, kv.key
    """
).bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))


async def weekly_stats(db: AsyncSession, user_id: uuid.UUID, today: date, weeks: int) -> dict:
    """Last `weeks` Monday-start weeks (including the current one), aggregated in SQL.

    Two round trips. Missing weeks are filled with zeros so the client always
    gets exactly `weeks` rows.
    """
    this_week = today - timedelta(days=today.weekday())
    start = this_week - timedelta(weeks=weeks - 1)
    params = {"uid": user_id, "start": start, "today": today, "present": PRESENT_MINUTES}
    rows = {r.week_start: r for r in (await db.execute(_WEEKS_SQL, params)).all()}
    topics = (await db.execute(_TOPIC_TOTALS_SQL, params)).mappings().all()

    out = []
    for i in range(weeks):
        ws = start + timedelta(weeks=i)
        r = rows.get(ws)
        out.append(
            {
                "week_start": ws,
                "total_minutes": r.total_minutes if r else 0,
                "present_days": r.present_days if r else 0,
                "by_topic": dict(r.by_topic) if r else {},
            }
        )
    return {"weeks": out, "topics": [dict(t) for t in topics]}
