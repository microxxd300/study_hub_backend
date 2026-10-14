import uuid

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import AuthUser
from app.deps import DB, forbid_demo
from app.errors import AppError, not_found
from app.models import Topic
from app.schemas import TopicCreate, TopicOut, TopicUpdate

router = APIRouter(prefix="/api/topics", tags=["topics"])

MAX_TOPICS = 30


@router.get("", response_model=list[TopicOut])
async def list_topics(user: AuthUser, db: DB) -> list[Topic]:
    # Bounded to 30 per user, so no pagination needed.
    rows = await db.scalars(
        select(Topic).where(Topic.user_id == user.id).order_by(Topic.created_at, Topic.id)
    )
    return list(rows)


@router.post("", response_model=TopicOut, status_code=status.HTTP_201_CREATED)
async def create_topic(body: TopicCreate, user: AuthUser, db: DB) -> Topic:
    forbid_demo(user, "Topics")
    count = await db.scalar(select(func.count()).select_from(Topic).where(Topic.user_id == user.id))
    if count >= MAX_TOPICS:
        raise AppError(
            409,
            "topic_limit",
            f"You can have at most {MAX_TOPICS} topics. Archive or rename one.",
        )
    topic = Topic(user_id=user.id, name=body.name, color=body.color)
    db.add(topic)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _integrity(exc) from exc
    return topic


@router.patch("/{topic_id}", response_model=TopicOut)
async def update_topic(topic_id: uuid.UUID, body: TopicUpdate, user: AuthUser, db: DB) -> Topic:
    forbid_demo(user, "Topics")
    topic = await db.scalar(select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id))
    if topic is None:
        raise not_found("Topic")
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(topic, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _integrity(exc) from exc
    return topic


def _integrity(exc: IntegrityError) -> AppError:
    if "uq_topics_user_name" in str(exc.orig):
        return AppError(409, "topic_exists", "You already have a topic with that name")
    if "profiles" in str(exc.orig):
        return AppError(409, "profile_missing", "Profile not initialised; call GET /api/me first")
    return AppError(409, "conflict", "Could not save topic")
