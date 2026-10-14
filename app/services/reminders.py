"""Streak saver: email users whose streak is alive but who haven't studied today."""

import uuid

from sqlalchemy import bindparam, text, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Profile
from app.services.email import send_batch, streak_email

# One statement for every user. "today" is each user's own local date. The
# streak ending yesterday is counted with the row-number trick: walking back
# from yesterday, a date belongs to the streak iff (yesterday - date) = rn - 1.
_CANDIDATES_SQL = text(
    """
    WITH local AS (
      SELECT p.id, p.email, p.display_name, (now() AT TIME ZONE p.timezone)::date AS today
      FROM profiles p
      WHERE p.reminders_enabled AND p.email IS NOT NULL
        AND (CAST(:demo AS uuid) IS NULL OR p.id <> :demo)
    )
    SELECT l.id, l.email, l.display_name, s.streak
    FROM local l
    CROSS JOIN LATERAL (
      SELECT COUNT(*) FILTER (WHERE (l.today - 1) - x.date = x.rn - 1) AS streak
      FROM (
        SELECT date, ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
        FROM daily_summary
        WHERE user_id = l.id AND date < l.today AND date >= l.today - 400 AND total_minutes >= :present
      ) x
    ) s
    WHERE s.streak > 0
      AND NOT EXISTS (
        SELECT 1 FROM daily_summary d
        WHERE d.user_id = l.id AND d.date = l.today AND d.total_minutes >= :present
      )
      AND NOT EXISTS (
        SELECT 1 FROM profiles p2 WHERE p2.id = l.id AND p2.last_reminder_on = l.today
      )
    """
).bindparams(bindparam("demo", type_=PG_UUID(as_uuid=True)))


async def send_streak_reminders(db: AsyncSession, app_url: str) -> int:
    rows = (await db.execute(_CANDIDATES_SQL, {"demo": get_settings().demo_user_id, "present": 15})).all()
    messages = [streak_email(r.email, r.display_name, r.streak, app_url) for r in rows]
    sent = await send_batch(messages)
    ids: list[uuid.UUID] = [rows[i].id for i in sent]
    if ids:
        await db.execute(
            update(Profile)
            .where(Profile.id.in_(ids))
            .values(last_reminder_on=text("(now() AT TIME ZONE profiles.timezone)::date"))
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return len(ids)
