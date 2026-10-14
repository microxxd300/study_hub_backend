"""Keyset (cursor) pagination.

The cursor is an opaque base64 of "<iso timestamp>|<uuid>" for the last row
returned. The next page is `WHERE (sort_col, id) < (ts, id)` on a composite
index, so page N costs the same as page 1 (no OFFSET scans).
"""

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated, Any, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, tuple_
from sqlalchemy.orm import InstrumentedAttribute

from app.errors import AppError

T = TypeVar("T")

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ):
        self.limit = limit
        self.cursor = decode_cursor(cursor) if cursor else None


def encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    raw = f"{ts.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts_raw, id_raw = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        return datetime.fromisoformat(ts_raw), uuid.UUID(id_raw)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(400, "bad_cursor", "Invalid pagination cursor") from exc


def paginate(
    stmt: Select,
    params: PageParams,
    sort_col: InstrumentedAttribute,
    id_col: InstrumentedAttribute,
    *,
    ascending: bool = False,
) -> Select:
    """Apply keyset filter + ordering + limit (fetches limit+1 to detect a next page)."""
    if params.cursor:
        ts, row_id = params.cursor
        key = tuple_(sort_col, id_col)
        stmt = stmt.where(key > tuple_(ts, row_id) if ascending else key < tuple_(ts, row_id))
    order = (sort_col.asc(), id_col.asc()) if ascending else (sort_col.desc(), id_col.desc())
    return stmt.order_by(*order).limit(params.limit + 1)


def build_page(rows: list[Any], params: PageParams, sort_key: str, item_fn=lambda r: r) -> dict:
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(getattr(last, sort_key), last.id)
    return {"items": [item_fn(r) for r in rows], "next_cursor": next_cursor}
