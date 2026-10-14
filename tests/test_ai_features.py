import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError

from app.ai import provider
from app.ai.provider import Usage
from app.db import SessionLocal
from app.models import AiUsage, Note
from app.services.command import regex_parse
from tests.conftest import DEMO_ID, User, queries
from tests.test_core import session_body


def _q(i: int) -> dict:
    return {
        "question": f"Question {i}?",
        "options": ["A", "B", "C", "D"],
        "answer_index": i % 4,
        "explanation": "x",
    }


SNAP_JSON = {
    "title": "Photosynthesis",
    "summary": "Plants turn light into chemical energy.",
    "key_points": ["Light reactions", "Calvin cycle", "Rubisco fixes carbon"],
    "flashcards": [{"front": f"Card {i}", "back": f"Answer {i}"} for i in range(6)],
    "quiz": [_q(i) for i in range(5)],
    "extracted_text": "Photosynthesis notes...",
}
QUIZ_JSON = {"title": "Cells", "questions": [_q(i) for i in range(5)]}
COACH_JSON = {
    "headline": "Solid week",
    "summary": "You studied a lot.",
    "wins": ["Consistency"],
    "focus_areas": ["Spread topics"],
    "plan": ["Review cards daily"],
}


class FakeGemini:
    def __init__(self, payload: dict | str, fail: bool = False):
        self.payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = 0
        self.fail = fail

    async def __call__(self, system, prompt, attachments, max_tokens):
        self.calls += 1
        if self.fail:
            raise provider._ProviderError("Gemini HTTP 503")
        half = len(self.payload) // 2
        yield self.payload[:half], None
        yield self.payload[half:], Usage(100, 200)


@pytest.fixture
def gemini(monkeypatch):
    def install(payload, fail=False):
        fake = FakeGemini(payload, fail)
        monkeypatch.setattr(provider, "_gemini_stream", fake)
        return fake

    return install


def sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def result_of(resp) -> dict:
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = sse_events(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "result", events
    return events[-1][1]


# ------------------------------------------------------------------ snap & study


async def test_snap_text_creates_note_cards_quiz_and_caches(user: User, gemini):
    fake = gemini(SNAP_JSON)
    topic = await user.topic("Biology")
    form = {"text": "Photosynthesis happens in chloroplasts...", "topic_id": topic["id"]}

    r = await user.post("/api/snap", data=form)
    assert r.status_code == 200, r.text
    events = sse_events(r.text)
    assert "delta" in [e for e, _ in events]
    res = events[-1][1]
    assert res["cached"] is False and res["flashcards"] == 6 and res["quiz_id"]

    note = (await user.get(f"/api/notes/{res['note_id']}")).json()
    assert len(note["flashcards"]) == 6 and len(note["quizzes"]) == 1
    assert note["source_text"] == form["text"]

    # Identical content again: served from the DB, no AI call.
    again = result_of(await user.post("/api/snap", data=form))
    assert again["cached"] is True and again["note_id"] == res["note_id"]
    assert fake.calls == 1

    async with SessionLocal() as db:
        usage = await db.scalar(select(AiUsage).where(AiUsage.user_id == user.id))
        assert usage.count == 1 and usage.tokens_out == 200


async def test_snap_rejects_unsupported_files(user: User, gemini):
    gemini(SNAP_JSON)
    r = await user.post(
        "/api/snap", files={"file": ("x.exe", b"MZ\x90\x00binary", "application/octet-stream")}
    )
    assert r.status_code == 415
    r = await user.post("/api/snap", data={})
    assert r.status_code == 422


async def test_snap_image_upload(user: User, gemini):
    fake = gemini(SNAP_JSON)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 2000
    res = result_of(await user.post("/api/snap", files={"file": ("n.jpg", jpeg, "image/jpeg")}))
    assert res["cached"] is False and fake.calls == 1


async def test_invalid_ai_json_falls_back_then_errors(user: User, gemini):
    gemini({"title": "missing everything"})  # fails schema validation; no Groq key -> error event
    r = await user.post("/api/snap", data={"text": "some notes here"})
    events = sse_events(r.text)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "ai_unavailable"


async def test_gemini_failure_uses_groq(user: User, gemini, monkeypatch):
    gemini(SNAP_JSON, fail=True)

    async def fake_groq(system, prompt, attachments, max_tokens):
        return json.dumps(QUIZ_JSON), Usage(10, 20)

    monkeypatch.setattr(provider, "_groq_complete", fake_groq)
    res = result_of(await user.post("/api/quizzes/generate", json={"topic_name": "Cell biology"}))
    assert res["title"] == "Cells" and res["cached"] is False


# ------------------------------------------------------------------ quizzes + SRS


async def test_quiz_topic_cache_is_shared(user: User, other_user: User, gemini):
    fake = gemini(QUIZ_JSON)
    a = result_of(await user.post("/api/quizzes/generate", json={"topic_name": "  The  Cold War "}))
    b = result_of(await user.post("/api/quizzes/generate", json={"topic_name": "the cold war"}))
    c = result_of(await other_user.post("/api/quizzes/generate", json={"topic_name": "The Cold War"}))
    assert fake.calls == 1
    assert b["cached"] and b["id"] == a["id"]
    assert c["cached"] and c["id"] != a["id"]  # copied into the other user's account


async def test_wrong_answers_become_flashcards(user: User, gemini):
    gemini(QUIZ_JSON)
    quiz = result_of(await user.post("/api/quizzes/generate", json={"topic_name": "Cells"}))
    answers = [q["answer_index"] for q in quiz["questions"]]
    answers[0] = (answers[0] + 1) % 4
    answers[3] = -1  # skipped counts as wrong
    r = await user.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers})
    assert r.json() == {"score": 3, "total": 5, "new_flashcards": 2, "best_score": 3}

    # Retaking with the same misses doesn't duplicate cards.
    r = await user.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers})
    assert r.json()["new_flashcards"] == 0

    due = await user.get("/api/flashcards/due")
    assert len(due.json()["items"]) == 2
    assert queries(due) <= 1
    count = await user.get("/api/flashcards/due/count")
    assert count.json() == {"due": 2}


async def test_spaced_repetition_ladder(user: User, gemini):
    gemini(SNAP_JSON)
    await user.post("/api/snap", data={"text": "notes for cards"})
    cards = (await user.get("/api/flashcards?limit=50")).json()["items"]
    card = cards[0]
    now = datetime.now(UTC)

    for stage, days in [(1, 1), (2, 3), (3, 7), (4, 14), (4, 14)]:
        r = await user.post(f"/api/flashcards/{card['id']}/review", json={"correct": True})
        body = r.json()
        due = datetime.fromisoformat(body["due_at"])
        assert body["stage"] == stage
        assert abs((due - now) - timedelta(days=days)) < timedelta(minutes=5)

    r = await user.post(f"/api/flashcards/{card['id']}/review", json={"correct": False})
    body = r.json()
    assert body["stage"] == 0
    assert datetime.fromisoformat(body["due_at"]) - now < timedelta(minutes=15)


async def test_flashcards_list_query_budget(user: User, other_user: User, gemini):
    gemini(SNAP_JSON)
    for i in range(3):
        await user.post("/api/snap", data={"text": f"notes {i}"})
    r = await user.get("/api/flashcards?limit=10")
    body = r.json()
    assert len(body["items"]) == 10 and body["next_cursor"]
    assert queries(r) <= 1
    r2 = await user.get(f"/api/flashcards?limit=10&cursor={body['next_cursor']}")
    assert len(r2.json()["items"]) == 8
    # IDOR
    card_id = body["items"][0]["id"]
    assert (
        await other_user.post(f"/api/flashcards/{card_id}/review", json={"correct": True})
    ).status_code == 404


async def test_note_detail_query_budget_and_lazy_raise(user: User, gemini):
    gemini(SNAP_JSON)
    res = result_of(await user.post("/api/snap", data={"text": "detail notes"}))
    r = await user.get(f"/api/notes/{res['note_id']}")
    assert queries(r) <= 3

    # lazy="raise": touching an unloaded relationship is an error, not a silent query.
    async with SessionLocal() as db:
        note = await db.scalar(select(Note).where(Note.user_id == user.id))
        with pytest.raises(InvalidRequestError):
            _ = note.flashcards


# ------------------------------------------------------------------ rate limits


async def test_ai_rate_limit_per_minute(user: User, gemini):
    gemini(QUIZ_JSON)
    for i in range(5):
        r = await user.post("/api/quizzes/generate", json={"topic_name": f"Topic number {i}"})
        assert r.status_code == 200
    r = await user.post("/api/quizzes/generate", json={"topic_name": "One more topic"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["error"]["code"] == "rate_limited"
    # cached results don't count against the limit
    r = await user.post("/api/quizzes/generate", json={"topic_name": "topic number 0"})
    assert r.status_code == 200


async def test_global_rate_limit(client):
    for _ in range(100):
        await client.get("/api/topics")
    r = await client.get("/api/topics")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert (await client.get("/health")).status_code == 200  # exempt


# ------------------------------------------------------------------ coach


async def test_coach_report_generated_once_and_cached(user: User, gemini):
    fake = gemini(COACH_JSON)
    t = await user.topic()
    today = datetime.now(UTC).date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    start = datetime(last_monday.year, last_monday.month, last_monday.day, 9, tzinfo=UTC)
    await user.post("/api/sessions", json=session_body(start, 50, t["id"]))

    dash = (await user.get("/api/dashboard")).json()
    assert dash["coach"]["status"] == "pending"

    res = result_of(await user.post("/api/coach/generate"))
    assert res["status"] == "ready" and res["report"]["report"]["headline"] == "Solid week"
    assert res["report"]["report"]["stats"]["total_minutes"] == 50

    again = result_of(await user.post("/api/coach/generate"))
    assert again["status"] == "ready" and fake.calls == 1
    assert (await user.get("/api/dashboard")).json()["coach"]["status"] == "ready"


async def test_coach_no_activity_skips_ai(user: User, gemini):
    fake = gemini(COACH_JSON)
    res = result_of(await user.post("/api/coach/generate"))
    assert res["status"] == "no_activity" and fake.calls == 0


# ------------------------------------------------------------------ command bar


@pytest.mark.parametrize(
    ("text", "minutes", "topic", "label"),
    [
        ("45 min science on photosynthesis", 45, "Science", "photosynthesis"),
        ("1h calculus", 60, "Calculus", None),
        ("1.5 hours of bible study", 90, "Bible Study", None),
        ("calc derivatives 30m", 30, "Calculus", "derivatives"),
        ("half an hour spanish: subjunctive", 30, "Spanish", "subjunctive"),
        ("study science", None, "Science", None),
        ("25 on the french revolution", 25, None, "the french revolution"),
    ],
)
def test_regex_parser(text, minutes, topic, label):
    topics = ["Science", "Calculus", "Bible Study", "Spanish"]
    p = regex_parse(text, topics)
    assert p.minutes == minutes
    assert (topics[p.topic_index] if p.topic_index is not None else None) == topic
    assert p.label == label


async def test_command_endpoint_regex_then_ai(user: User, gemini):
    await user.topic("Science")
    r = await user.post("/api/command/parse", json={"text": "45 min science on photosynthesis"})
    assert r.json()["source"] == "regex" and r.json()["minutes"] == 45 and r.json()["topic_name"] == "Science"
    assert r.json()["topic_id"]

    fake = gemini({"minutes": 40, "topic": "Science", "label": "cells"})
    r = await user.post("/api/command/parse", json={"text": "gonna do bio-ish stuff, cells and things"})
    assert r.json()["source"] == "ai" and r.json()["minutes"] == 40
    await user.post("/api/command/parse", json={"text": "gonna do bio-ish stuff, cells and things"})
    assert fake.calls == 1  # cached


# ------------------------------------------------------------------ daily job + demo


async def test_internal_daily_requires_secret(client):
    assert (await client.post("/internal/daily")).status_code == 403
    assert (await client.post("/internal/daily", headers={"X-Cron-Secret": "nope"})).status_code == 403


async def test_daily_job_seeds_demo_and_demo_is_guarded(client, gemini):
    r = await client.post("/internal/daily", headers={"X-Cron-Secret": "cron-test-secret"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["keepalive"] and body["demo"]["sessions"] > 150 and body["demo"]["coach_reports"] >= 1

    demo = User(client, DEMO_ID)
    dash = await demo.get("/api/dashboard")
    data = dash.json()
    assert data["profile"]["is_demo"] is True
    assert data["current_streak"] >= 20 and data["longest_streak"] >= data["current_streak"]
    assert len(data["topics"]) == 5 and data["due_cards"] > 0
    assert data["coach"]["status"] == "ready"
    assert queries(dash) <= 5

    assert (await demo.post("/api/topics", json={"name": "Spam", "color": "snow"})).status_code == 403

    gemini(QUIZ_JSON)
    for i in range(3):
        assert (await demo.post("/api/quizzes/generate", json={"topic_name": f"demo {i}"})).status_code == 200
    r = await demo.post("/api/quizzes/generate", json={"topic_name": "demo 4"})
    assert r.status_code == 429 and "demo" in r.json()["error"]["message"]


async def test_streak_reminder_candidates(client, user: User, monkeypatch):
    from app.services import reminders

    sent: list[dict] = []

    async def fake_send(messages):
        sent.extend(messages)
        return list(range(len(messages)))

    monkeypatch.setattr(reminders, "send_batch", fake_send)
    t = await user.topic()
    yesterday = datetime.now(UTC) - timedelta(days=1)
    start = yesterday.replace(hour=10, minute=0, second=0, microsecond=0)
    await user.post("/api/sessions", json=session_body(start, 30, t["id"]))

    r = await client.post("/internal/daily", headers={"X-Cron-Secret": "cron-test-secret"})
    assert r.json()["reminders_sent"] >= 1
    mine = [m for m in sent if m["to"] == [user.email]]
    assert mine and "1 day streak" in mine[0]["subject"]

    # Not sent twice on the same day.
    sent.clear()
    await client.post("/internal/daily", headers={"X-Cron-Secret": "cron-test-secret"})
    assert sent == []
