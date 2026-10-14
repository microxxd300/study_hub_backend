from typing import Annotated

from fastapi import APIRouter, Header

from app.auth import AuthUser
from app.deps import DB, forbid_demo
from app.schemas import ProfileOut, ProfileUpdate
from app.services.profiles import get_or_create_profile

router = APIRouter(prefix="/api/me", tags=["profile"])


def profile_out(profile, is_demo: bool) -> ProfileOut:
    out = ProfileOut.model_validate(profile)
    out.is_demo = is_demo
    return out


@router.get("", response_model=ProfileOut)
async def get_me(
    user: AuthUser,
    db: DB,
    x_timezone: Annotated[str | None, Header(max_length=64)] = None,
) -> ProfileOut:
    """Bootstrap call made right after login: creates the profile on first visit."""
    profile = await get_or_create_profile(db, user, x_timezone)
    return profile_out(profile, user.is_demo)


@router.patch("", response_model=ProfileOut)
async def update_me(body: ProfileUpdate, user: AuthUser, db: DB) -> ProfileOut:
    forbid_demo(user, "The demo profile")
    profile = await get_or_create_profile(db, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "display_name":
            setattr(profile, field, value)
    await db.commit()
    return profile_out(profile, user.is_demo)
