import hashlib
import json

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.ai import prompts
from app.ai.provider import AIError, generate_json
from app.ai.usage import record_usage
from app.auth import AuthUser
from app.deps import DB
from app.errors import AppError
from app.models import AiCache, Topic
from app.rate_limit import enforce_ai_quota
from app.schemas import AiCommandResult, CommandParseIn, CommandParseOut
from app.services.command import DEFAULT_MINUTES, regex_parse

router = APIRouter(prefix="/api/command", tags=["command"])


@router.post("/parse", response_model=CommandParseOut)
async def parse_command(body: CommandParseIn, request: Request, user: AuthUser, db: DB) -> dict:
    rows = (
        await db.execute(
            select(Topic.id, Topic.name)
            .where(Topic.user_id == user.id, Topic.archived.is_(False))
            .order_by(Topic.name)
        )
    ).all()
    names = [r.name for r in rows]

    parsed = regex_parse(body.text, names)
    if parsed.minutes is not None or parsed.topic_index is not None:
        topic = rows[parsed.topic_index] if parsed.topic_index is not None else None
        return {
            "minutes": parsed.minutes or DEFAULT_MINUTES,
            "topic_id": topic.id if topic else None,
            "topic_name": topic.name if topic else None,
            "label": parsed.label,
            "source": "regex",
        }

    # Regex found nothing usable: ask the AI (cached on text + topic list).
    key = hashlib.sha256(
        json.dumps(["command", " ".join(body.text.lower().split()), names]).encode()
    ).hexdigest()
    cached = await db.scalar(select(AiCache.value).where(AiCache.key == key))
    if cached is not None:
        result = AiCommandResult.model_validate(cached)
    else:
        enforce_ai_quota(request, user)
        try:
            result, _, usage = await generate_json(
                system=prompts.COMMAND_SYSTEM,
                prompt=prompts.command_prompt(body.text, names),
                schema=AiCommandResult,
                max_tokens=200,
            )
        except AIError as exc:
            raise AppError(503, "ai_unavailable", str(exc)) from exc
        await db.execute(
            insert(AiCache)
            .values(key=key, kind="command", value=result.model_dump())
            .on_conflict_do_nothing(index_elements=[AiCache.key])
        )
        await record_usage(db, user.id, usage)
        await db.commit()

    by_name = {r.name.lower(): r for r in rows}
    topic = by_name.get((result.topic or "").lower())
    if result.minutes is None and topic is None:
        raise AppError(422, "unparseable", 'Try something like "45 min calculus on derivatives"')
    return {
        "minutes": result.minutes or DEFAULT_MINUTES,
        "topic_id": topic.id if topic else None,
        "topic_name": topic.name if topic else None,
        "label": result.label,
        "source": "ai",
    }
