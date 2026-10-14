import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import Usage
from app.models import AiUsage


async def record_usage(db: AsyncSession, user_id: uuid.UUID, usage: Usage) -> None:
    """Per-user, per-day AI usage log (calls + tokens). Caller commits."""
    stmt = insert(AiUsage).values(
        user_id=user_id,
        date=datetime.now(UTC).date(),
        count=1,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ai_usage_user_date",
        set_={
            "count": AiUsage.count + 1,
            "tokens_in": AiUsage.tokens_in + stmt.excluded.tokens_in,
            "tokens_out": AiUsage.tokens_out + stmt.excluded.tokens_out,
        },
    )
    await db.execute(stmt)
