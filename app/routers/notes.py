import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.auth import AuthUser
from app.deps import DB, forbid_demo
from app.errors import not_found
from app.models import Note, Quiz
from app.pagination import Page, PageParams, build_page, paginate
from app.schemas import NoteDetail, NoteListItem

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("", response_model=Page[NoteListItem])
async def list_notes(
    user: AuthUser,
    db: DB,
    page: Annotated[PageParams, Depends()],
    topic_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    # Only small columns: summary / source_text are fetched by the detail endpoint.
    stmt = select(Note.id, Note.title, Note.topic_id, Note.source_kind, Note.created_at).where(
        Note.user_id == user.id
    )
    if topic_id:
        stmt = stmt.where(Note.topic_id == topic_id)
    rows = (await db.execute(paginate(stmt, page, Note.created_at, Note.id))).all()
    return build_page(rows, page, "created_at")


@router.get("/{note_id}", response_model=NoteDetail)
async def get_note(note_id: uuid.UUID, user: AuthUser, db: DB) -> Note:
    # 3 queries total regardless of card count: note, its cards, its quizzes.
    note = await db.scalar(
        select(Note)
        .where(Note.id == note_id, Note.user_id == user.id)
        .options(
            selectinload(Note.flashcards),
            selectinload(Note.quizzes).load_only(
                Quiz.id,
                Quiz.title,
                Quiz.topic_id,
                Quiz.note_id,
                Quiz.attempts,
                Quiz.best_score,
                Quiz.created_at,
            ),
        )
    )
    if note is None:
        raise not_found("Note")
    note.flashcards.sort(key=lambda c: (c.created_at, str(c.id)))
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: uuid.UUID, user: AuthUser, db: DB) -> None:
    forbid_demo(user, "Demo notes")
    # Cards and quizzes generated from the note go with it (FK ON DELETE CASCADE).
    result = await db.execute(delete(Note).where(Note.id == note_id, Note.user_id == user.id))
    if result.rowcount == 0:
        raise not_found("Note")
    await db.commit()
