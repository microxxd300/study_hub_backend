"""Snap & Study: image/PDF (or pasted text) -> summary, key points, flashcards, quiz.

Uploads are never stored. Images arrive already resized by the browser (max
~1600px JPEG); PDFs are capped at 3 MB / 10 pages. The bytes go to Gemini and
are dropped when the request ends; only the extracted text and the generated
study aids are saved. Identical content returns the cached note (no AI call).
"""

import hashlib
import io
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ai import prompts
from app.ai.provider import AIError, Attachment, stream_json
from app.ai.usage import record_usage
from app.auth import AuthUser, CurrentUser
from app.db import SessionLocal
from app.deps import DB
from app.errors import AppError, not_found
from app.models import Flashcard, Note, Quiz, Topic
from app.rate_limit import enforce_ai_quota
from app.schemas import AiSnapResult
from app.sse import single_result, sse, sse_response

router = APIRouter(prefix="/api/snap", tags=["snap"])

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 3 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_TEXT_CHARS = 20_000


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _pdf_pages(data: bytes) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AppError(422, "pdf_encrypted", "Password-protected PDFs aren't supported")
        return len(reader.pages)
    except PdfReadError as exc:
        raise AppError(422, "bad_pdf", "That PDF couldn't be read") from exc


def _result_payload(note: Note, quiz_id: uuid.UUID | None, card_count: int, cached: bool) -> dict:
    return {
        "note_id": note.id,
        "title": note.title,
        "summary": note.summary,
        "key_points": note.key_points,
        "flashcards": card_count,
        "quiz_id": quiz_id,
        "cached": cached,
    }


@router.post("")
async def snap(
    request: Request,
    user: AuthUser,
    db: DB,
    topic_id: Annotated[uuid.UUID | None, Form()] = None,
    text: Annotated[str | None, Form(max_length=MAX_TEXT_CHARS)] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> StreamingResponse:
    if (file is None) == (not text):
        raise AppError(422, "validation_error", "Send either a file or some text")

    attachments: list[Attachment] = []
    if file is not None:
        data = await file.read(MAX_IMAGE_BYTES + 1)
        await file.close()
        mime = _sniff(data)
        if mime is None:
            raise AppError(415, "unsupported_file", "Upload a JPEG, PNG, WebP image or a PDF")
        limit = MAX_PDF_BYTES if mime == "application/pdf" else MAX_IMAGE_BYTES
        if len(data) > limit:
            raise AppError(
                413,
                "payload_too_large",
                f"File is too large (max {limit // (1024 * 1024)} MB)",
            )
        if mime == "application/pdf" and _pdf_pages(data) > MAX_PDF_PAGES:
            raise AppError(413, "too_many_pages", f"PDFs can have at most {MAX_PDF_PAGES} pages")
        attachments.append(Attachment(mime, data))
        kind = "pdf" if mime == "application/pdf" else "image"
        content_hash = hashlib.sha256(data).hexdigest()
    else:
        kind = "text"
        content_hash = hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()

    topic_name = None
    if topic_id:
        topic_name = await db.scalar(select(Topic.name).where(Topic.id == topic_id, Topic.user_id == user.id))
        if topic_name is None:
            raise not_found("Topic")

    # Cache: identical upload -> return the existing note, never regenerate.
    cached = await db.scalar(select(Note).where(Note.user_id == user.id, Note.content_hash == content_hash))
    if cached:
        quiz_id = await db.scalar(
            select(Quiz.id).where(Quiz.user_id == user.id, Quiz.cache_key == f"note:{cached.id}")
        )
        count = len((await db.scalars(select(Flashcard.id).where(Flashcard.note_id == cached.id))).all())
        return sse_response(single_result(_result_payload(cached, quiz_id, count, cached=True)))

    enforce_ai_quota(request, user, snap=True)
    await db.close()  # don't hold a pooled connection during the AI call
    return sse_response(
        _stream(
            user,
            topic_id,
            topic_name,
            kind,
            content_hash,
            attachments,
            text if kind == "text" else None,
        )
    )


async def _stream(
    user: CurrentUser,
    topic_id: uuid.UUID | None,
    topic_name: str | None,
    kind: str,
    content_hash: str,
    attachments: list[Attachment],
    pasted_text: str | None,
) -> AsyncIterator[str]:
    yield sse("status", {"stage": "reading", "message": "Reading your notes"})
    try:
        async for event in stream_json(
            system=prompts.STUDY_SYSTEM,
            prompt=prompts.snap_prompt(topic_name, pasted_text),
            schema=AiSnapResult,
            attachments=attachments,
        ):
            if event.kind == "delta":
                yield sse("delta", {"text": event.text})
            elif event.kind == "restart":
                yield sse("restart", {"provider": event.provider})
            elif event.kind == "result" and event.result is not None:
                yield sse(
                    "status",
                    {"stage": "saving", "message": "Saving flashcards and quiz"},
                )
                payload = await _save(
                    user,
                    topic_id,
                    kind,
                    content_hash,
                    event.result,
                    pasted_text,
                    event.usage,
                )
                yield sse("result", payload)
    except AIError as exc:
        yield sse("error", {"code": "ai_unavailable", "message": str(exc)})
    finally:
        attachments.clear()  # drop upload bytes as soon as we're done


async def _save(user, topic_id, kind, content_hash, result: AiSnapResult, pasted_text, usage) -> dict:
    note_id = uuid.uuid4()
    note = Note(
        id=note_id,
        user_id=user.id,
        topic_id=topic_id,
        title=result.title,
        summary=result.summary,
        key_points=result.key_points,
        source_text=pasted_text or result.extracted_text or None,
        source_kind=kind,
        content_hash=content_hash,
    )
    cards = [
        Flashcard(
            user_id=user.id,
            topic_id=topic_id,
            note_id=note_id,
            front=c.front,
            back=c.back,
            origin="ai",
        )
        for c in result.flashcards
    ]
    quiz = Quiz(
        id=uuid.uuid4(),
        user_id=user.id,
        topic_id=topic_id,
        note_id=note_id,
        title=f"Quiz: {result.title}"[:120],
        questions=[q.model_dump() for q in result.quiz],
        cache_key=f"note:{note_id}",
    )
    async with SessionLocal() as db:
        try:
            db.add(note)
            await db.flush()  # note row must exist before its cards/quiz (FKs)
            db.add_all([*cards, quiz])
            await record_usage(db, user.id, usage)
            await db.commit()
        except IntegrityError:
            # Same content saved concurrently: return that note instead.
            await db.rollback()
            existing = await db.scalar(
                select(Note).where(Note.user_id == user.id, Note.content_hash == content_hash)
            )
            if existing is None:
                raise
            return _result_payload(existing, None, 0, cached=True)
    return _result_payload(note, quiz.id, len(cards), cached=False)
