import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, func, select

from app.auth import AuthUser
from app.deps import DB, forbid_demo
from app.errors import not_found
from app.models import Flashcard
from app.pagination import Page, PageParams, build_page, paginate
from app.schemas import FlashcardOut, FlashcardReview
from app.services.srs import review_card

router = APIRouter(prefix="/api/flashcards", tags=["flashcards"])

_COLUMNS = (
    Flashcard.id,
    Flashcard.front,
    Flashcard.back,
    Flashcard.topic_id,
    Flashcard.note_id,
    Flashcard.origin,
    Flashcard.stage,
    Flashcard.due_at,
    Flashcard.created_at,
)


@router.get("", response_model=Page[FlashcardOut])
async def list_flashcards(
    user: AuthUser,
    db: DB,
    page: Annotated[PageParams, Depends()],
    topic_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    stmt = select(*_COLUMNS).where(Flashcard.user_id == user.id)
    if topic_id:
        stmt = stmt.where(Flashcard.topic_id == topic_id)
    rows = (await db.execute(paginate(stmt, page, Flashcard.created_at, Flashcard.id))).all()
    return build_page(rows, page, "created_at")


@router.get("/due", response_model=Page[FlashcardOut])
async def due_flashcards(user: AuthUser, db: DB, page: Annotated[PageParams, Depends()]) -> dict:
    """Cards due now, oldest-due first, keyset-paginated on (due_at, id)."""
    stmt = select(*_COLUMNS).where(Flashcard.user_id == user.id, Flashcard.due_at <= func.now())
    rows = (await db.execute(paginate(stmt, page, Flashcard.due_at, Flashcard.id, ascending=True))).all()
    return build_page(rows, page, "due_at")


@router.get("/due/count")
async def due_count(user: AuthUser, db: DB) -> dict:
    count = await db.scalar(
        select(func.count())
        .select_from(Flashcard)
        .where(Flashcard.user_id == user.id, Flashcard.due_at <= func.now())
    )
    return {"due": count}


@router.post("/{card_id}/review", response_model=FlashcardOut)
async def review(card_id: uuid.UUID, body: FlashcardReview, user: AuthUser, db: DB) -> Flashcard:
    card = await review_card(db, user.id, card_id, body.correct)
    if card is None:
        raise not_found("Flashcard")
    await db.commit()
    return card


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flashcard(card_id: uuid.UUID, user: AuthUser, db: DB) -> None:
    forbid_demo(user, "Demo flashcards")
    result = await db.execute(delete(Flashcard).where(Flashcard.id == card_id, Flashcard.user_id == user.id))
    if result.rowcount == 0:
        raise not_found("Flashcard")
    await db.commit()
