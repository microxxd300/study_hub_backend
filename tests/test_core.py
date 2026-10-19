import uuid
from datetime import UTC, datetime, timedelta

from tests.conftest import User, make_token


def session_body(start: datetime, minutes: int = 25, topic_id: str | None = None, **kw) -> dict:
    return {
        "id": str(kw.pop("id", uuid.uuid4())),
        "topic_id": topic_id,
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(minutes=minutes + 2)).isoformat(),
        "focus_minutes": minutes,
        **kw,
    }


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


async def test_auth_required_and_error_envelope(client):
    r = await client.get("/api/topics")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "Missing bearer token"}}

    r = await client.get("/api/topics", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401

    expired = make_token(uuid.uuid4())
    r = await client.get("/api/topics", headers={"Authorization": f"Bearer {expired}x"})
    assert r.status_code == 401


async def test_cors_only_allows_known_origins(client):
    ok = await client.options(
        "/api/topics",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = await client.options(
        "/api/topics",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_profile_bootstrap_and_update(user: User):
    r = await user.get("/api/me")
    assert r.json()["timezone"] == "UTC" and r.json()["onboarded"] is False
    r = await user.patch(
        "/api/me", json={"timezone": "Asia/Manila", "onboarded": True, "display_name": "Kai"}
    )
    assert r.status_code == 200
    assert r.json()["timezone"] == "Asia/Manila"
    r = await user.patch("/api/me", json={"timezone": "Mars/Olympus"})
    assert r.status_code == 422
    r = await user.patch("/api/me", json={"is_admin": True})
    assert r.status_code == 422  # extra="forbid"


async def test_buddy_mode(user: User):
    assert (await user.get("/api/me")).json()["buddy_mode"] == "on"
    r = await user.patch("/api/me", json={"buddy_mode": "quiet"})
    assert r.status_code == 200 and r.json()["buddy_mode"] == "quiet"
    assert (await user.patch("/api/me", json={"buddy_mode": "loud"})).status_code == 422


async def test_daily_goal(user: User):
    assert (await user.get("/api/me")).json()["daily_goal_minutes"] == 60
    r = await user.patch("/api/me", json={"daily_goal_minutes": 90})
    assert r.status_code == 200 and r.json()["daily_goal_minutes"] == 90
    assert (await user.patch("/api/me", json={"daily_goal_minutes": 2})).status_code == 422
    assert (await user.patch("/api/me", json={"daily_goal_minutes": 1000})).status_code == 422


async def test_topics_crud_and_validation(user: User, other_user: User):
    t = await user.topic("Calculus", "iris")
    assert t["name"] == "Calculus" and t["archived"] is False

    dup = await user.post("/api/topics", json={"name": "calculus", "color": "lime"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "topic_exists"

    too_long = await user.post("/api/topics", json={"name": "x" * 41, "color": "lime"})
    assert too_long.status_code == 422
    bad_color = await user.post("/api/topics", json={"name": "Art", "color": "#ff0000"})
    assert bad_color.status_code == 422

    r = await user.patch(f"/api/topics/{t['id']}", json={"name": "Calc II", "color": "sky", "archived": True})
    assert r.json()["name"] == "Calc II" and r.json()["archived"] is True

    # IDOR: another user can't touch it, and doesn't see it.
    r = await other_user.patch(f"/api/topics/{t['id']}", json={"name": "pwned"})
    assert r.status_code == 404
    assert (await other_user.get("/api/topics")).json() == []


async def test_topic_limit(user: User):
    for i in range(30):
        await user.topic(f"T{i}")
    r = await user.post("/api/topics", json={"name": "One too many", "color": "snow"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "topic_limit"


async def test_save_session_updates_daily_summary(user: User):
    topic = await user.topic()
    start = datetime.now(UTC) - timedelta(hours=3)
    r = await user.post("/api/sessions", json=session_body(start, 25, topic["id"]))
    assert r.status_code == 201, r.text
    assert r.json()["created"] is True and r.json()["day_minutes"] == 25

    r = await user.post("/api/sessions", json=session_body(start + timedelta(minutes=40), 30, topic["id"]))
    assert r.json()["day_minutes"] == 55

    hm = (await user.get("/api/heatmap")).json()
    day = next(d for d in hm["days"] if d["d"] == r.json()["date"])
    assert day["m"] == 55 and day["t"] == {topic["id"]: 55}


async def test_session_save_is_idempotent(user: User):
    body = session_body(datetime.now(UTC) - timedelta(hours=2), 25)
    first = await user.post("/api/sessions", json=body)
    retry = await user.post("/api/sessions", json=body)
    assert first.status_code == 201 and retry.status_code == 200
    assert retry.json()["created"] is False and retry.json()["day_minutes"] == 25


async def test_session_validation(user: User, other_user: User):
    now = datetime.now(UTC)
    future = await user.post("/api/sessions", json=session_body(now + timedelta(hours=1)))
    assert future.status_code == 422 and future.json()["error"]["code"] == "future_session"

    long = session_body(now - timedelta(hours=14), 25)
    long["ended_at"] = (now - timedelta(minutes=5)).isoformat()
    assert (await user.post("/api/sessions", json=long)).status_code == 422

    naive = session_body(now - timedelta(hours=1))
    naive["started_at"] = naive["started_at"].replace("+00:00", "")
    assert (await user.post("/api/sessions", json=naive)).status_code == 422

    base = now - timedelta(hours=5)
    assert (await user.post("/api/sessions", json=session_body(base, 30))).status_code == 201
    overlap = await user.post("/api/sessions", json=session_body(base + timedelta(minutes=10), 30))
    assert overlap.status_code == 409 and overlap.json()["error"]["code"] == "session_overlap"

    # Someone else's topic id is rejected (no IDOR via foreign keys).
    theirs = await other_user.topic("Theirs")
    r = await user.post("/api/sessions", json=session_body(now - timedelta(hours=8), 25, theirs["id"]))
    assert r.status_code == 404

    # Someone else's session id can't be hijacked through the idempotency path.
    mine = session_body(now - timedelta(hours=10), 25)
    assert (await other_user.post("/api/sessions", json=mine)).status_code == 201
    assert (await user.post("/api/sessions", json=mine)).status_code == 409
