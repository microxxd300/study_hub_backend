from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.models import Profile


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_today(tz: str) -> date:
    return datetime.now(zone(tz)).date()


async def get_or_create_profile(db: AsyncSession, user: CurrentUser, tz_hint: str | None = None) -> Profile:
    """Profiles are created lazily on first contact (id = Supabase user id)."""
    profile = await db.scalar(select(Profile).where(Profile.id == user.id))
    if profile is not None:
        return profile
    tz = tz_hint if tz_hint and zone(tz_hint).key == tz_hint else "UTC"
    stmt = (
        insert(Profile)
        .values(id=user.id, email=user.email, timezone=tz)
        .on_conflict_do_nothing(index_elements=[Profile.id])
        .returning(Profile)
    )
    profile = await db.scalar(stmt)
    if profile is None:  # created concurrently by another request
        profile = await db.scalar(select(Profile).where(Profile.id == user.id))
    await db.commit()
    return profile
