"""Server-Sent Events helpers for streamed AI responses.

Event protocol used by every AI endpoint:
  event: status  data: {"stage": "...", "message": "..."}
  event: delta   data: {"text": "..."}         raw model output as it streams
  event: restart data: {"provider": "groq"}    primary failed mid-stream; clear the buffer
  event: result  data: {...}                   final validated + saved payload
  event: error   data: {"code": "...", "message": "..."}
"""

import json
from collections.abc import AsyncIterator

from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'))}\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


async def single_result(data: object) -> AsyncIterator[str]:
    """For cache hits: same protocol, no AI call."""
    yield sse("result", data)
