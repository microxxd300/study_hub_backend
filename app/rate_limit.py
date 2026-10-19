"""Rate limiting (slowapi + limits, Upstash Redis storage in production).

- General: 100 requests / 15 min per IP (applied by middleware to every route).
- AI: 5/min and 20/day per user; Snap & Study additionally 5/day per user.
- Demo account: it is shared, so AI is limited per visitor IP to 3/day.
"""

import logging
import math
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse_many
from slowapi import Limiter
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import CurrentUser
from app.config import get_settings
from app.errors import AppError, error_body

log = logging.getLogger("studyhub.ratelimit")


def client_ip(request: Request) -> str:
    """Client IP as seen by our trusted proxy (Render).

    Proxies *append* to X-Forwarded-For, so the left-most entries are whatever
    the client sent and can be spoofed. Count `trusted_proxy_hops` from the right.
    """
    hops = get_settings().trusted_proxy_hops
    fwd = request.headers.get("x-forwarded-for")
    if fwd and hops > 0:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[-min(hops, len(parts))]
    return request.client.host if request.client else "unknown"


limiter = Limiter(
    key_func=client_ip,
    storage_uri=get_settings().redis_url,
    strategy="fixed-window",
    in_memory_fallback_enabled=True,
    key_prefix="studyhub",
    enabled=get_settings().rate_limit_enabled,
)

GLOBAL_LIMIT = parse_many("100/15minutes")[0]
GLOBAL_EXEMPT_PREFIXES = ("/health", "/internal/")
AI_LIMITS = parse_many("5/minute;20/day")
SNAP_LIMITS = parse_many("5/day")
DEMO_AI_LIMITS = parse_many("3/day")


def _retry_after(item: RateLimitItem, *identifiers: str) -> int:
    reset_at, _ = limiter.limiter.get_window_stats(item, *identifiers)
    return max(1, math.ceil(reset_at - time.time()))


def _too_many(retry_after: int, message: str) -> AppError:
    return AppError(429, "rate_limited", message, headers={"Retry-After": str(retry_after)})


def enforce_ai_quota(request: Request, user: CurrentUser, *, snap: bool = False) -> None:
    """Consume one AI call for this user. Raises 429 with Retry-After when exhausted.

    Checks every window before consuming any, so a rejected call doesn't burn quota.
    """
    if not limiter.enabled:
        return
    checks: list[tuple[RateLimitItem, str, str]] = []
    if user.is_demo:
        ip = client_ip(request)
        checks += [(item, "demo-ai", ip) for item in DEMO_AI_LIMITS]
    checks += [(item, "ai", str(user.id)) for item in AI_LIMITS]
    if snap:
        checks += [(item, "snap", str(user.id)) for item in SNAP_LIMITS]

    try:
        _consume(checks)
    except AppError:
        raise
    except Exception:
        log.exception("Rate-limit storage unavailable; allowing AI call")


def _consume(checks: list[tuple[RateLimitItem, str, str]]) -> None:
    for item, scope, ident in checks:
        if not limiter.limiter.test(item, scope, ident):
            if scope == "demo-ai":
                msg = "The demo allows 3 AI calls per day. Sign up for your own free account to keep going."
            elif scope == "snap":
                msg = "You've used all 5 Snap & Study scans for today. They reset tomorrow."
            elif item.GRANULARITY.name == "minute":
                msg = "Slow down a little: AI is limited to 5 requests per minute."
            else:
                msg = "You've reached today's AI limit (20 requests). It resets tomorrow."
            raise _too_many(_retry_after(item, scope, ident), msg)
    for item, scope, ident in checks:
        limiter.limiter.hit(item, scope, ident)


class GlobalRateLimitMiddleware:
    """100 requests / 15 min per IP on every route except /health and /internal.

    slowapi's own middleware resolves endpoints by scanning app.routes, which
    misses routes inside FastAPI's included routers, so we apply the global
    limit here using the same limiter + storage.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or scope["path"].startswith(GLOBAL_EXEMPT_PREFIXES)
            or not limiter.enabled
        ):
            return await self.app(scope, receive, send)
        ip = client_ip(Request(scope))
        try:
            allowed = limiter.limiter.hit(GLOBAL_LIMIT, "global", ip)
        except Exception:  # noqa: BLE001 - fail open if Redis is unreachable
            allowed = True
        if not allowed:
            response = JSONResponse(
                error_body(
                    "rate_limited",
                    "Too many requests. Please wait a moment and try again.",
                ),
                status_code=429,
                headers={"Retry-After": str(_retry_after(GLOBAL_LIMIT, "global", ip))},
            )
            return await response(scope, receive, send)
        await self.app(scope, receive, send)
