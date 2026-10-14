"""SM-2 style spaced repetition with a fixed ladder: 1, 3, 7, 14 days.

`stage` = consecutive correct reviews. A correct answer schedules the card
INTERVALS[stage] days out and moves it up a rung; a miss drops it to stage 0
and brings it back in 10 minutes. Nothing runs in the background: "due" is
evaluated at read time as `due_at <= now()` on the (user_id, due_at) index.
"""

import uuid

from sqlalchemy import case, cast, func, literal, update
from sqlalchemy.dialects.postgresql import INTERVAL as INTERVAL_TYPE
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Flashcard

INTERVAL_DAYS = (1, 3, 7, 14)
RELEARN_MINUTES = 10


def _interval_for_stage():
    whens = [(Flashcard.stage == i, literal(f"{d} days")) for i, d in enumerate(INTERVAL_DAYS[:-1])]
    return cast(case(*whens, else_=literal(f"{INTERVAL_DAYS[-1]} days")), INTERVAL_TYPE())


async def review_card(
    db: AsyncSession, user_id: uuid.UUID, card_id: uuid.UUID, correct: bool
) -> Flashcard | None:
    """Apply one review in a single UPDATE ... RETURNING (no read-modify-write race)."""
    if correct:
        values = {
            "stage": func.least(Flashcard.stage + 1, len(INTERVAL_DAYS)),
            "due_at": func.now() + _interval_for_stage(),
        }
    else:
        values = {
            "stage": 0,
            "lapses": Flashcard.lapses + 1,
            "due_at": func.now() + cast(literal(f"{RELEARN_MINUTES} minutes"), INTERVAL_TYPE()),
        }
    stmt = (
        update(Flashcard)
        .where(Flashcard.id == card_id, Flashcard.user_id == user_id)
        .values(reviews=Flashcard.reviews + 1, last_reviewed_at=func.now(), **values)
        .returning(Flashcard)
        .execution_options(synchronize_session=False)
    )
    return await db.scalar(stmt)
