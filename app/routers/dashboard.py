import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.auth import AuthUser
from app.deps import DB
from app.errors import not_found
from app.models import CoachReport, Topic
from app.routers.me import profile_out
from app.schemas import CoachReportOut, CoachState, DashboardOut, HeatmapOut
from app.services.coach import week_bounds
from app.services.profiles import get_or_create_profile, local_today
from app.services.stats import heatmap_days, streak_stats

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(user: AuthUser, db: DB) -> dict:
    """Everything the home screen needs in ONE request.

    Five small queries run sequentially on one AsyncSession (it isn't safe for
    concurrent use): profile, topics, heatmap rows, stats (streaks + totals +
    due cards + last week's minutes in one statement), latest coach report.
    """
    profile = await get_or_create_profile(db, user)
    today = local_today(profile.timezone)
    last_week_start, this_week_start = week_bounds(today)

    topics = list(
        await db.scalars(select(Topic).where(Topic.user_id == user.id).order_by(Topic.created_at, Topic.id))
    )
    days = await heatmap_days(db, user.id, today)
    stats = await streak_stats(
        db,
        user.id,
        today,
        with_dashboard_extras=True,
        last_week=(last_week_start, this_week_start),
    )
    latest = await db.scalar(
        select(CoachReport)
        .where(CoachReport.user_id == user.id)
        .order_by(CoachReport.week_start.desc())
        .limit(1)
    )

    latest_out = CoachReportOut.model_validate(latest) if latest else None
    if latest and latest.week_start == last_week_start:
        coach = CoachState(status="ready", week_start=last_week_start, report=latest_out)
    elif stats["last_week_minutes"] > 0:
        coach = CoachState(status="pending", week_start=last_week_start, report=latest_out)
    elif latest_out:
        coach = CoachState(status="no_activity", week_start=last_week_start, report=latest_out)
    else:
        coach = CoachState(status="none")

    today_minutes = days[-1]["m"] if days and days[-1]["d"] == today else 0
    return {
        "profile": profile_out(profile, user.is_demo),
        "today": today,
        "today_minutes": today_minutes,
        "current_streak": stats["current_streak"],
        "longest_streak": stats["longest_streak"],
        "total_minutes": stats["total_minutes"],
        "present_days": stats["present_days"],
        "due_cards": stats["due_cards"],
        "topics": topics,
        "heatmap": days,
        "coach": coach,
    }


@router.get("/heatmap", response_model=HeatmapOut)
async def heatmap(user: AuthUser, db: DB, topic_id: Annotated[uuid.UUID | None, Query()] = None) -> dict:
    """Heatmap + streak stats, optionally filtered to one topic (3 queries)."""
    profile = await get_or_create_profile(db, user)
    today = local_today(profile.timezone)
    if topic_id is not None:
        owned = await db.scalar(select(Topic.id).where(Topic.id == topic_id, Topic.user_id == user.id))
        if owned is None:
            raise not_found("Topic")
    days = await heatmap_days(db, user.id, today, topic_id)
    stats = await streak_stats(db, user.id, today, topic_id)
    return {**stats, "days": days, "today": today, "topic_id": topic_id}
