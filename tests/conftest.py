"""Test setup: a real Postgres (the queries use jsonb, ON CONFLICT, window functions).

Set TEST_DATABASE_URL, or run the bundled container:
    docker compose up -d db   # then the default URL below works
"""

import os
import uuid

# Configure settings BEFORE the app (and its engine) is imported.
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:54329/studyhub_test"
)
JWT_SECRET = "test-secret-at-least-32-bytes-long-000000"
DEMO_ID = uuid.UUID("00000000-0000-4000-8000-00000000dead")
os.environ.update(
    ENV="test",
    DATABASE_URL=TEST_DB,
    SUPABASE_URL="",
    SUPABASE_JWT_SECRET=JWT_SECRET,
    REDIS_URL="memory://",
    CRON_SECRET="cron-test-secret",
    DEMO_USER_ID=str(DEMO_ID),
    GEMINI_API_KEY="test-key",
    GROQ_API_KEY="",
    RESEND_API_KEY="",
)

import time  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def make_token(user_id: uuid.UUID, email: str | None = "student@example.com") -> str:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now,
        "role": "authenticated",
    }
    if email:
        claims["email"] = email
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    """Fresh schema per test run via the real Alembic migration."""
    from alembic.config import Config

    from alembic import command

    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB
    import asyncio

    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield
    from app.db import engine as app_engine

    await app_engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limits():
    from app.rate_limit import limiter

    limiter.reset()
    yield


@pytest.fixture
async def client():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class User:
    def __init__(self, client: httpx.AsyncClient, user_id: uuid.UUID | None = None):
        self.id = user_id or uuid.uuid4()
        self.client = client
        self.email = f"{self.id}@example.com"
        self.headers = {"Authorization": f"Bearer {make_token(self.id, self.email)}"}

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        return await self.client.request(method, url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    async def get(self, url: str, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw):
        return await self.request("POST", url, **kw)

    async def patch(self, url: str, **kw):
        return await self.request("PATCH", url, **kw)

    async def delete(self, url: str, **kw):
        return await self.request("DELETE", url, **kw)

    async def topic(self, name: str = "Science", color: str = "lime") -> dict:
        r = await self.post("/api/topics", json={"name": name, "color": color})
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
async def user(client) -> User:
    u = User(client)
    r = await u.get("/api/me", headers={"X-Timezone": "UTC"})
    assert r.status_code == 200, r.text
    return u


@pytest.fixture
async def other_user(client) -> User:
    u = User(client)
    await u.get("/api/me")
    return u


def queries(resp: httpx.Response) -> int:
    return int(resp.headers["X-Query-Count"])
