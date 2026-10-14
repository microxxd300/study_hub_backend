import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.ai import prompts
from app.ai.provider import AIError, stream_json
from app.ai.usage import record_usage
from app.auth import AuthUser, CurrentUser
from app.db import SessionLocal
from app.deps import DB
from app.errors import AppError, not_found
from app.models import Flashcard, Note, Quiz, QuizAttempt, Topic
from app.pagination import Page, PageParams, build_page, paginate
from app.rate_limit import enforce_ai_quota
from app.schemas import (
    AiQuizResult,
    QuizDetail,
    QuizGenerate,
    QuizListItem,
    QuizResultOut,
    QuizSubmit,
)
from app.sse import single_result, sse, sse_response

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])

_LIST_COLUMNS = (
    Quiz.id,
    Quiz.title,
    Quiz.topic_id,
    Quiz.note_id,
    Quiz.attempts,
    Quiz.best_score,
    Quiz.created_at,
)


@router.get("", response_model=Page[QuizListItem])
async def list_quizzes(
    user: AuthUser,
    db: DB,
    page: Annotated[PageParams, Depends()],
    topic_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    stmt = select(*_LIST_COLUMNS).where(Quiz.user_id == user.id)  # questions jsonb only in detail
    if topic_id:
        stmt = stmt.where(Quiz.topic_id == topic_id)
    rows = (await db.execute(paginate(stmt, page, Quiz.created_at, Quiz.id))).all()
    return build_page(rows, page, "created_at")


@router.get("/{quiz_id}", response_model=QuizDetail)
async def get_quiz(quiz_id: uuid.UUID, user: AuthUser, db: DB) -> Quiz:
    quiz = await db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == user.id))
    if quiz is None:
        raise not_found("Quiz")
    return quiz


def _normalise(name: str) -> str:
    return " ".join(name.lower().split())


@router.post("/generate")
async def generate_quiz(body: QuizGenerate, request: Request, user: AuthUser, db: DB) -> StreamingResponse:
    """Quiz from a saved note or a topic name. Cached: identical requests never hit the AI."""
    topic_id = body.topic_id
    if topic_id and not await db.scalar(
        select(Topic.id).where(Topic.id == topic_id, Topic.user_id == user.id)
    ):
        raise not_found("Topic")

    note = None
    if body.note_id:
        note = await db.scalar(select(Note).where(Note.id == body.note_id, Note.user_id == user.id))
        if note is None:
            raise not_found("Note")
        cache_key = f"note:{note.id}"
        topic_id = topic_id or note.topic_id
        prompt = prompts.quiz_from_note_prompt(note.title, note.summary, note.key_points, note.source_text)
    else:
        cache_key = f"topic:{_normalise(body.topic_name)}"[:160]
        prompt = prompts.quiz_from_topic_prompt(body.topic_name)

    mine = await db.scalar(select(Quiz).where(Quiz.user_id == user.id, Quiz.cache_key == cache_key))
    if mine:
        return sse_response(single_result({**_detail(mine), "cached": True}))

    if body.topic_name:
        # Topic-name quizzes aren't personal: reuse anyone's cached copy.
        shared = await db.scalar(select(Quiz).where(Quiz.cache_key == cache_key).limit(1))
        if shared:
            copy = await _insert_quiz(db, user.id, topic_id, None, shared.title, shared.questions, cache_key)
            await db.commit()
            return sse_response(single_result({**_detail(copy), "cached": True}))

    enforce_ai_quota(request, user)
    await db.close()
    return sse_response(_stream(user, topic_id, note.id if note else None, cache_key, prompt))


async def _stream(
    user: CurrentUser,
    topic_id: uuid.UUID | None,
    note_id: uuid.UUID | None,
    cache_key: str,
    prompt: str,
) -> AsyncIterator[str]:
    yield sse("status", {"stage": "writing", "message": "Writing your quiz"})
    try:
        async for event in stream_json(
            system=prompts.STUDY_SYSTEM,
            prompt=prompt,
            schema=AiQuizResult,
            max_tokens=2048,
        ):
            if event.kind == "delta":
                yield sse("delta", {"text": event.text})
            elif event.kind == "restart":
                yield sse("restart", {"provider": event.provider})
            elif event.kind == "result" and event.result is not None:
                result: AiQuizResult = event.result
                async with SessionLocal() as db:
                    quiz = await _insert_quiz(
                        db,
                        user.id,
                        topic_id,
                        note_id,
                        result.title,
                        [q.model_dump() for q in result.questions],
                        cache_key,
                    )
                    await record_usage(db, user.id, event.usage)
                    await db.commit()
                yield sse("result", {**_detail(quiz), "cached": False})
    except AIError as exc:
        yield sse("error", {"code": "ai_unavailable", "message": str(exc)})


async def _insert_quiz(db, user_id, topic_id, note_id, title, questions, cache_key) -> Quiz:
    stmt = (
        insert(Quiz)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            topic_id=topic_id,
            note_id=note_id,
            title=title,
            questions=questions,
            cache_key=cache_key,
        )
        .on_conflict_do_nothing(constraint="uq_quizzes_user_cache_key")
        .returning(Quiz)
    )
    quiz = await db.scalar(stmt)
    if quiz is None:  # generated concurrently
        quiz = await db.scalar(select(Quiz).where(Quiz.user_id == user_id, Quiz.cache_key == cache_key))
    return quiz


def _detail(quiz: Quiz) -> dict:
    return QuizDetail.model_validate(quiz).model_dump(mode="json")


@router.post("/{quiz_id}/submit", response_model=QuizResultOut)
async def submit_quiz(quiz_id: uuid.UUID, body: QuizSubmit, user: AuthUser, db: DB) -> dict:
    """Grade answers; each missed question becomes a flashcard (due now)."""
    quiz = await db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == user.id).with_for_update())
    if quiz is None:
        raise not_found("Quiz")
    questions = quiz.questions
    if len(body.answers) != len(questions):
        raise AppError(422, "validation_error", f"Expected {len(questions)} answers")

    wrong = [q for q, a in zip(questions, body.answers, strict=True) if a != q["answer_index"]]
    score = len(questions) - len(wrong)

    new_cards = 0
    if wrong:
        fronts = [q["question"][:300] for q in wrong]
        existing = set(
            await db.scalars(
                select(Flashcard.front).where(Flashcard.user_id == user.id, Flashcard.front.in_(fronts))
            )
        )
        rows = [
            {
                "user_id": user.id,
                "topic_id": quiz.topic_id,
                "note_id": quiz.note_id,
                "front": q["question"][:300],
                "back": _card_back(q),
                "origin": "wrong_answer",
            }
            for q in wrong
            if q["question"][:300] not in existing
        ]
        if rows:
            await db.execute(insert(Flashcard), rows)
            new_cards = len(rows)
        if existing:  # already a card: bring it back to the front of the queue
            await db.execute(
                update(Flashcard)
                .where(Flashcard.user_id == user.id, Flashcard.front.in_(existing))
                .values(stage=0, due_at=func.now())
            )

    db.add(QuizAttempt(user_id=user.id, quiz_id=quiz.id, score=score, total=len(questions)))
    quiz.attempts += 1
    quiz.best_score = max(quiz.best_score or 0, score)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError(409, "conflict", "Could not save quiz result") from exc
    return {
        "score": score,
        "total": len(questions),
        "new_flashcards": new_cards,
        "best_score": quiz.best_score,
    }


def _card_back(q: dict) -> str:
    answer = q["options"][q["answer_index"]]
    explanation = q.get("explanation") or ""
    return f"{answer}. {explanation}".strip()[:1000]
