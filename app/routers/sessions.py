import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.auth import AuthUser
from app.deps import DB
from app.models import StudySession
from app.pagination import Page, PageParams, build_page, paginate
from app.schemas import SessionCreate, SessionOut, SessionSaved
from app.services.sessions import save_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_LIST_COLUMNS = (
    StudySession.id,
    StudySession.topic_id,
    StudySession.started_at,
    StudySession.ended_at,
    StudySession.focus_minutes,
    StudySession.distractions,
    StudySession.label,
)


@router.post("", response_model=SessionSaved, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, user: AuthUser, db: DB, response: Response) -> dict:
    result = await save_session(db, user, body)
    if not result["created"]:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("", response_model=Page[SessionOut])
async def list_sessions(
    user: AuthUser,
    db: DB,
    page: Annotated[PageParams, Depends()],
    topic_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    stmt = select(*_LIST_COLUMNS).where(StudySession.user_id == user.id)
    if topic_id:
        stmt = stmt.where(StudySession.topic_id == topic_id)
    rows = (await db.execute(paginate(stmt, page, StudySession.started_at, StudySession.id))).all()
    return build_page(rows, page, "started_at")
