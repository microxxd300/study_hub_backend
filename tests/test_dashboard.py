from datetime import UTC, datetime, timedelta

from tests.conftest import User, queries
from tests.test_core import session_body


async def _seed_days(user: User, topic_id: str, days_ago: list[int], minutes: int = 20) -> None:
    now = datetime.now(UTC)
    for d in days_ago:
        day = (now - timedelta(days=d)).date()
        start = datetime(day.year, day.month, day.day, 0, 5, tzinfo=UTC)
        if d == 0:
            start = now - timedelta(minutes=minutes + 5)
        r = await user.post("/api/sessions", json=session_body(start, minutes, topic_id))
        assert r.status_code == 201, r.text


async def test_dashboard_streaks_and_query_budget(user: User):
    topic = await user.topic("Physics", "sky")
    other = await user.topic("Art", "rose")
    await _seed_days(user, topic["id"], [0, 1, 2, 3, 4])  # current streak: 5
    await _seed_days(user, topic["id"], list(range(10, 17)))  # longest streak: 7
    await _seed_days(user, other["id"], [6], minutes=10)  # under 15 min: not "present"

    r = await user.get("/api/dashboard")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_streak"] == 5
    assert data["longest_streak"] == 7
    assert data["present_days"] == 12
    assert data["total_minutes"] == 12 * 20 + 10
    assert data["today_minutes"] == 20
    assert len(data["topics"]) == 2 and len(data["heatmap"]) == 13
    assert data["coach"]["status"] in {"pending", "none"}

    # The whole home screen in at most 5 queries, independent of data volume.
    assert queries(r) <= 5, f"dashboard ran {queries(r)} queries"


async def test_streak_survives_until_end_of_today(user: User):
    t = await user.topic()
    await _seed_days(user, t["id"], [1, 2, 3])  # nothing yet today
    data = (await user.get("/api/dashboard")).json()
    assert data["current_streak"] == 3 and data["today_minutes"] == 0


async def test_heatmap_topic_filter(user: User, other_user: User):
    a = await user.topic("A")
    b = await user.topic("B")
    await _seed_days(user, a["id"], [1, 2])
    await _seed_days(user, b["id"], [3])
    r = await user.get(f"/api/heatmap?topic_id={a['id']}")
    data = r.json()
    assert [d["m"] for d in data["days"]] == [20, 20]
    assert data["current_streak"] == 2 and data["total_minutes"] == 40
    assert queries(r) <= 4
    # can't probe someone else's topic
    assert (await other_user.get(f"/api/heatmap?topic_id={a['id']}")).status_code == 404


async def test_sessions_cursor_pagination(user: User):
    t = await user.topic()
    base = datetime.now(UTC) - timedelta(days=3)
    created = []
    for i in range(45):
        r = await user.post("/api/sessions", json=session_body(base + timedelta(minutes=30 * i), 25, t["id"]))
        assert r.status_code == 201
        created.append(r.json()["session"]["id"])

    seen, cursor, pages = [], None, 0
    while True:
        url = "/api/sessions?limit=20" + (f"&cursor={cursor}" if cursor else "")
        r = await user.get(url)
        assert r.status_code == 200
        assert queries(r) <= 1, f"sessions list ran {queries(r)} queries"
        body = r.json()
        seen += [s["id"] for s in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 45 and len(set(seen)) == 45
    assert seen == created[::-1]  # newest first, no gaps or repeats across pages

    assert (await user.get("/api/sessions?limit=51")).status_code == 422
    assert (await user.get("/api/sessions?cursor=garbage")).json()["error"]["code"] == "bad_cursor"


async def test_weekly_stats(user: User):
    a = await user.topic("Alpha")
    b = await user.topic("Beta")
    await _seed_days(user, a["id"], [0, 1], minutes=30)
    await _seed_days(user, b["id"], [8], minutes=20)  # previous week or earlier

    r = await user.get("/api/stats/weekly?weeks=4")
    assert r.status_code == 200, r.text
    assert queries(r) <= 3, f"weekly stats ran {queries(r)} queries"
    data = r.json()
    weeks = data["weeks"]
    assert len(weeks) == 4
    starts = [w["week_start"] for w in weeks]
    assert starts == sorted(starts)  # oldest first, Mondays
    from datetime import date as _d

    assert all(_d.fromisoformat(s).weekday() == 0 for s in starts)
    assert sum(w["total_minutes"] for w in weeks) == 80
    assert {t["topic_id"]: t["minutes"] for t in data["topics"]} == {a["id"]: 60, b["id"]: 20}
    assert data["topics"][0]["topic_id"] == a["id"]  # most minutes first

    assert (await user.get("/api/stats/weekly?weeks=27")).status_code == 422
