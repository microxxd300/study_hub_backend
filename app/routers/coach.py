from collections.abc import AsyncIterator
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.ai import prompts
from app.ai.provider import AIError, stream_json
from app.ai.usage import record_usage
from app.auth import AuthUser, CurrentUser
from app.db import SessionLocal
from app.deps import DB
from app.rate_limit import enforce_ai_quota
from app.schemas import AiCoachResult, CoachReportOut
from app.services.coach import cached_report, save_report, week_bounds, week_stats
from app.services.profiles import get_or_create_profile, local_today
from app.sse import single_result, sse, sse_response

router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.post("/generate")
async def generate_coach_report(request: Request, user: AuthUser, db: DB) -> StreamingResponse:
    """Generate (or return the cached) report for the last finished week, as SSE."""
    profile = await get_or_create_profile(db, user)
    week_start, _ = week_bounds(local_today(profile.timezone))

    existing = await cached_report(db, user.id, week_start)
    if existing:
        return sse_response(
            single_result({"status": "ready", "report": CoachReportOut.model_validate(existing)})
        )

    stats = await week_stats(db, user.id, profile.timezone, week_start)
    if stats["total_minutes"] == 0:
        # Nothing to coach on: no AI call, nothing cached.
        return sse_response(single_result({"status": "no_activity", "week_start": week_start}))

    enforce_ai_quota(request, user)
    await db.close()  # release the pooled connection before the (slow) AI stream
    return sse_response(_stream(user, week_start, stats))


async def _stream(user: CurrentUser, week_start: date, stats: dict) -> AsyncIterator[str]:
    yield sse("status", {"stage": "thinking", "message": "Reviewing your week"})
    try:
        async for event in stream_json(
            system=prompts.COACH_SYSTEM,
            prompt=prompts.coach_prompt(stats),
            schema=AiCoachResult,
            max_tokens=1024,
        ):
            if event.kind == "delta":
                yield sse("delta", {"text": event.text})
            elif event.kind == "restart":
                yield sse("restart", {"provider": event.provider})
            elif event.kind == "result" and event.result is not None:
                result: AiCoachResult = event.result
                report = {
                    **result.model_dump(),
                    "stats": stats,
                    "provider": event.provider,
                }
                async with SessionLocal() as db:
                    saved = await save_report(db, user.id, week_start, result.summary, report)
                    await record_usage(db, user.id, event.usage)
                    await db.commit()
                yield sse(
                    "result",
                    {"status": "ready", "report": CoachReportOut.model_validate(saved)},
                )
    except AIError as exc:
        yield sse("error", {"code": "ai_unavailable", "message": str(exc)})
