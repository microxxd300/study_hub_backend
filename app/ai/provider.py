"""Single entry point for every AI call.

Gemini (Flash, multimodal) is primary. On a 429/5xx, network error, missing key
or a response that fails schema validation we fall back to Groq. Output tokens
are capped on every call, and every result is validated against a Pydantic
schema before anyone sees it.

Usage:
    async for event in stream_json(system=..., prompt=..., schema=AiQuizResult):
        event.kind in {"delta", "restart", "result"}
"""

import base64
import io
import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings

log = logging.getLogger("studyhub.ai")

M = TypeVar("M", bound=BaseModel)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class AIError(Exception):
    """All providers failed (or none is configured)."""


class _ProviderError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Attachment:
    mime: str  # image/jpeg, image/png, image/webp, application/pdf
    data: bytes


@dataclass(slots=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(slots=True)
class AIEvent[M: BaseModel]:
    kind: Literal["delta", "restart", "result"]
    text: str = ""
    result: M | None = None
    provider: str = ""
    usage: Usage = field(default_factory=Usage)


# ---------------------------------------------------------------- helpers

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json[M: BaseModel](raw: str, schema: type[M]) -> M:
    cleaned = _FENCE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise _ProviderError("No JSON object in response")
    try:
        return schema.model_validate(json.loads(cleaned[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise _ProviderError(f"Invalid JSON from model: {exc}") from exc


def pdf_text(data: bytes, max_pages: int = 10, max_chars: int = 30000) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    chunks = []
    for page in reader.pages[:max_pages]:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()[:max_chars]


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


# ---------------------------------------------------------------- Gemini


async def _gemini_stream(
    system: str, prompt: str, attachments: Sequence[Attachment], max_tokens: int
) -> AsyncIterator[tuple[str, Usage | None]]:
    s = get_settings()
    if not s.gemini_api_key:
        raise _ProviderError("Gemini not configured")
    parts: list[dict] = [
        {"inlineData": {"mimeType": a.mime, "data": base64.b64encode(a.data).decode()}} for a in attachments
    ]
    parts.append({"text": prompt})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens,
            "temperature": 0.4,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    async with (
        httpx.AsyncClient(timeout=s.ai_timeout_seconds) as client,
        client.stream(
            "POST",
            GEMINI_URL.format(model=s.gemini_model),
            headers={"x-goog-api-key": s.gemini_api_key},
            json=body,
        ) as resp,
    ):
        if resp.status_code != 200:
            detail = (await resp.aread())[:300]
            raise _ProviderError(f"Gemini HTTP {resp.status_code}: {detail!r}")
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:])
            usage = None
            if meta := chunk.get("usageMetadata"):
                usage = Usage(
                    meta.get("promptTokenCount", 0),
                    meta.get("candidatesTokenCount", 0),
                )
            text = "".join(
                p.get("text", "")
                for c in chunk.get("candidates", [])[:1]
                for p in c.get("content", {}).get("parts", [])
            )
            yield text, usage


# ---------------------------------------------------------------- Groq (fallback)


async def _groq_complete(
    system: str, prompt: str, attachments: Sequence[Attachment], max_tokens: int
) -> tuple[str, Usage]:
    s = get_settings()
    if not s.groq_api_key:
        raise _ProviderError("Groq not configured")
    model = s.groq_model
    content: list[dict] | str = prompt
    images = [a for a in attachments if a.mime.startswith("image/")]
    pdfs = [a for a in attachments if a.mime == "application/pdf"]
    if pdfs:  # Groq can't read PDFs: extract text locally (no OCR for scanned PDFs)
        extracted = "\n\n".join(pdf_text(p.data) for p in pdfs)
        if not extracted:
            raise _ProviderError("PDF has no extractable text")
        prompt = f"{prompt}\n\nSOURCE NOTES (extracted from PDF):\n{extracted}"
        content = prompt
    if images:
        model = s.groq_vision_model
        content = [{"type": "text", "text": prompt}] + [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{a.mime};base64,{base64.b64encode(a.data).decode()}"},
            }
            for a in images
        ]
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=s.ai_timeout_seconds) as client:
        resp = await client.post(GROQ_URL, headers={"Authorization": f"Bearer {s.groq_api_key}"}, json=body)
    if resp.status_code != 200:
        raise _ProviderError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    usage = data.get("usage", {})
    return data["choices"][0]["message"]["content"], Usage(
        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    )


# ---------------------------------------------------------------- public API


async def stream_json[M: BaseModel](
    *,
    system: str,
    prompt: str,
    schema: type[M],
    attachments: Sequence[Attachment] = (),
    max_tokens: int | None = None,
) -> AsyncIterator[AIEvent[M]]:
    max_tokens = min(
        max_tokens or get_settings().ai_max_output_tokens,
        get_settings().ai_max_output_tokens,
    )

    # 1) Gemini, streamed
    buf: list[str] = []
    usage = Usage()
    try:
        async for text, u in _gemini_stream(system, prompt, attachments, max_tokens):
            if u:
                usage = u
            if text:
                buf.append(text)
                yield AIEvent("delta", text=text, provider="gemini")
        result = parse_json("".join(buf), schema)
        yield AIEvent("result", result=result, provider="gemini", usage=usage)
        return
    except (_ProviderError, httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        log.warning("Gemini failed, falling back to Groq: %s", exc)

    # 2) Groq, single shot
    if buf:
        yield AIEvent("restart", provider="groq")
    try:
        raw, usage = await _groq_complete(system, prompt, attachments, max_tokens)
        yield AIEvent("delta", text=raw, provider="groq")
        yield AIEvent("result", result=parse_json(raw, schema), provider="groq", usage=usage)
    except (_ProviderError, httpx.HTTPError, KeyError, IndexError) as exc:
        log.error("Groq fallback failed: %s", exc)
        raise AIError("The AI service is busy right now. Please try again in a minute.") from exc


async def generate_json[M: BaseModel](
    *,
    system: str,
    prompt: str,
    schema: type[M],
    attachments: Sequence[Attachment] = (),
    max_tokens: int | None = None,
) -> tuple[M, str, Usage]:
    """Non-streaming convenience wrapper."""
    async for event in stream_json(
        system=system,
        prompt=prompt,
        schema=schema,
        attachments=attachments,
        max_tokens=max_tokens,
    ):
        if event.kind == "result" and event.result is not None:
            return event.result, event.provider, event.usage
    raise AIError("No result from AI")
