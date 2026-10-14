"""Endpoints called by cron-job.org, protected by the X-Cron-Secret header."""

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import text

from app.config import get_settings
from app.deps import DB
from app.errors import AppError

log = logging.getLogger("studyhub.internal")


def require_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    secret = get_settings().cron_secret
    if not secret or not x_cron_secret or not hmac.compare_digest(secret, x_cron_secret):
        raise AppError(403, "forbidden", "Forbidden")


router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_cron_secret)],
)


@router.post("/daily")
async def daily(db: DB) -> dict:
    """Daily at 12:00 UTC: Supabase keep-alive, demo reset, streak-saver emails."""
    from app.demo.seed import reset_demo
    from app.services.reminders import send_streak_reminders

    settings = get_settings()
    result: dict = {}

    # 1) Keep-alive: any query counts as activity for Supabase's free-tier pause timer.
    await db.execute(text("SELECT 1"))
    result["keepalive"] = True

    # 2) Demo reset
    if settings.demo_user_id:
        try:
            result["demo"] = await reset_demo(db, settings.demo_user_id)
        except Exception:
            log.exception("Demo reset failed")
            await db.rollback()
            result["demo"] = "failed"

    # 3) Streak-saver emails
    try:
        result["reminders_sent"] = await send_streak_reminders(db, settings.app_url)
    except Exception:
        log.exception("Streak reminders failed")
        await db.rollback()
        result["reminders_sent"] = "failed"

    return result
