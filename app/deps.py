from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db import get_db
from app.errors import AppError

DB = Annotated[AsyncSession, Depends(get_db)]


def forbid_demo(user: CurrentUser, what: str = "This") -> None:
    """The demo account is shared by every visitor, so shared settings are read-only."""
    if user.is_demo:
        raise AppError(
            403,
            "demo_readonly",
            f"{what} is read-only in the demo. Sign up to make it yours.",
        )
