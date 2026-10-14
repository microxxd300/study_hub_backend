"""Supabase JWT verification.

The frontend sends `Authorization: Bearer <supabase access token>`. Tokens are
verified locally on every request against Supabase's JWKS (asymmetric keys,
cached in memory) or, for legacy projects, the shared HS256 secret. No cookies
are used, so there is no CSRF surface.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Request

from app.config import get_settings
from app.errors import AppError

_ASYMMETRIC_ALGS = {"RS256", "ES256", "EdDSA"}
_JWKS_TTL = 600.0  # seconds
_JWKS_MIN_REFRESH = 30.0  # don't refetch more often than this on unknown kid


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str | None
    is_demo: bool


class _JwksCache:
    def __init__(self) -> None:
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> object | None:
        now = time.monotonic()
        if kid not in self._keys or now - self._fetched_at > _JWKS_TTL:
            async with self._lock:
                stale = now - self._fetched_at > _JWKS_TTL
                if stale or (kid not in self._keys and now - self._fetched_at > _JWKS_MIN_REFRESH):
                    await self._refresh()
        return self._keys.get(kid)

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(get_settings().jwks_url)
            resp.raise_for_status()
        keys = {}
        for jwk in resp.json().get("keys", []):
            try:
                keys[jwk["kid"]] = jwt.PyJWK(jwk).key
            except (jwt.PyJWKError, KeyError):
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()


_jwks = _JwksCache()


def _unauthorized(message: str = "Invalid or expired session") -> AppError:
    return AppError(401, "unauthorized", message, headers={"WWW-Authenticate": "Bearer"})


async def verify_token(token: str) -> dict:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized() from exc

    alg = header.get("alg")
    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise _unauthorized()
        key: object = settings.supabase_jwt_secret
    elif alg in _ASYMMETRIC_ALGS and header.get("kid"):
        try:
            key = await _jwks.get(header["kid"])
        except httpx.HTTPError as exc:
            raise AppError(503, "auth_unavailable", "Could not verify session, try again") from exc
        if key is None:
            raise _unauthorized()
    else:
        raise _unauthorized()

    try:
        return jwt.decode(
            token,
            key,  # type: ignore[arg-type]
            algorithms=[alg],
            audience=settings.supabase_jwt_audience,
            issuer=settings.jwt_issuer if settings.supabase_url else None,
            options={"require": ["exp", "sub"]},
            leeway=10,
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized() from exc


async def get_current_user(request: Request) -> CurrentUser:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Missing bearer token")
    claims = await verify_token(token)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (ValueError, KeyError) as exc:
        raise _unauthorized() from exc
    user = CurrentUser(
        id=user_id,
        email=claims.get("email"),
        is_demo=user_id == get_settings().demo_user_id,
    )
    request.state.user = user  # used by rate-limit key functions
    return user


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
