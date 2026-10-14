"""Reset + seed the shared demo account.

Run manually:   python -m app.demo.seed
Run daily via:  POST /internal/daily (cron-job.org)

Seeds ~6 months of sessions across 5 topics, the daily_summary rows (built in
SQL from those sessions), plus pre-generated notes, flashcards, quizzes, quiz
attempts and coach reports, so the demo never needs a live AI call to look alive.
"""

import asyncio
import random
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import bindparam, delete, insert, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.demo import fixtures
from app.models import (
    CoachReport,
    Flashcard,
    Note,
    Profile,
    Quiz,
    QuizAttempt,
    StudySession,
    Topic,
)
from app.services.coach import week_bounds, week_stats

DAYS = 182
DEMO_TZ = "UTC"  # visitors come from everywhere; UTC keeps "today" predictable

REBUILD_SUMMARY_SQL = text(
    """
    WITH per_topic AS (
      SELECT (started_at AT TIME ZONE :tz)::date AS d,
             COALESCE(topic_id::text, 'none') AS tk,
             SUM(focus_minutes)::int AS m,
             COUNT(*)::int AS c
      FROM study_sessions WHERE user_id = :uid
      GROUP BY 1, 2
    )
    INSERT INTO daily_summary (user_id, date, total_minutes, session_count, minutes_by_topic)
    SELECT :uid, d, SUM(m), SUM(c), jsonb_object_agg(tk, m)
    FROM per_topic GROUP BY d
    """
).bindparams(bindparam("uid", type_=PG_UUID(as_uuid=True)))


def _sessions(user_id: uuid.UUID, topic_ids: dict[str, uuid.UUID], now: datetime) -> list[dict]:
    rng = random.Random(1789)
    today = now.date()
    names = [t["name"] for t in fixtures.TOPICS]
    weights = [t["weight"] for t in fixtures.TOPICS]
    # A deliberate break ~6 weeks ago so longest streak > current streak.
    gap = {today - timedelta(days=d) for d in range(38, 41)}
    rows = []
    for back in range(DAYS, 0, -1):
        day = today - timedelta(days=back)
        recent = back <= 24  # keep a solid current streak
        p = 0.95 if recent else (0.78 if day.weekday() < 5 else 0.5)
        if day in gap or rng.random() > p:
            continue
        cursor = datetime.combine(day, time(hour=rng.choice([7, 8, 9, 13, 16, 18, 19])), UTC)
        for _ in range(rng.choice([1, 1, 2, 2, 2, 3, 4])):
            topic = rng.choices(names, weights)[0]
            minutes = rng.choice([25, 25, 25, 30, 45, 50, 60])
            start = cursor + timedelta(minutes=rng.randint(0, 20))
            end = start + timedelta(minutes=minutes + rng.randint(0, 6))
            if end.date() != day:
                break
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "topic_id": topic_ids[topic],
                    "started_at": start,
                    "ended_at": end,
                    "focus_minutes": minutes,
                    "distractions": rng.choices([0, 1, 2, 3, 5], [40, 30, 15, 10, 5])[0],
                    "label": rng.choice(fixtures.LABELS[topic]),
                }
            )
            cursor = end + timedelta(minutes=rng.choice([5, 10, 30, 90]))
    # A session earlier today, if there's room, so "today" isn't empty.
    morning = datetime.combine(today, time(6, 0), UTC)
    if now - morning > timedelta(hours=1):
        rows.append(
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "topic_id": topic_ids["Calculus"],
                "started_at": morning,
                "ended_at": morning + timedelta(minutes=27),
                "focus_minutes": 25,
                "distractions": 1,
                "label": "chain rule",
            }
        )
    return rows


def _coach_report(stats: dict) -> tuple[str, dict]:
    hours = stats["total_minutes"] / 60
    top = stats["topics"][0]["topic"] if stats["topics"] else "your topics"
    prev = stats["prev_week_minutes"]
    delta = stats["total_minutes"] - prev
    trend = f"up {delta} min on the week before" if delta >= 0 else f"{-delta} min down on the week before"
    summary = (
        f"You focused for {hours:.1f}h across {stats['present_days']} days, {trend}. "
        f"{top} got the most attention."
    )[:280]
    report = {
        "headline": f"{hours:.1f} hours, {stats['present_days']}/7 days present",
        "summary": summary,
        "wins": [
            f"Showed up on {stats['present_days']} of 7 days.",
            f"Most time went to {top} — consistent depth builds mastery.",
            "Kept sessions in focused 25-50 minute blocks.",
        ],
        "focus_areas": [
            "Spread time more evenly: lighter topics are falling behind.",
            "Review due flashcards before starting new material.",
        ],
        "plan": [
            "Start each day with 10 minutes of flashcard review.",
            f"Book two 45-minute {top} sessions early in the week.",
            "Take one quiz per topic to find weak spots.",
            "Protect your streak with a short session on busy days.",
        ],
        "stats": stats,
        "provider": "pre-generated",
    }
    return summary, report


async def reset_demo(db: AsyncSession, user_id: uuid.UUID) -> dict:
    now = datetime.now(UTC)
    # Everything hangs off the profile with ON DELETE CASCADE.
    await db.execute(delete(Profile).where(Profile.id == user_id))
    await db.execute(
        insert(Profile).values(
            id=user_id,
            display_name="Demo Student",
            timezone=DEMO_TZ,
            reminders_enabled=False,
            onboarded=True,
        )
    )

    topic_ids = {t["name"]: uuid.uuid4() for t in fixtures.TOPICS}
    await db.execute(
        insert(Topic),
        [
            {
                "id": topic_ids[t["name"]],
                "user_id": user_id,
                "name": t["name"],
                "color": t["color"],
                "created_at": now - timedelta(days=DAYS + 1 - i),
            }
            for i, t in enumerate(fixtures.TOPICS)
        ],
    )

    sessions = _sessions(user_id, topic_ids, now)
    await db.execute(insert(StudySession), sessions)
    await db.execute(REBUILD_SUMMARY_SQL, {"uid": user_id, "tz": DEMO_TZ})

    rng = random.Random(42)
    notes, cards, quizzes, attempts = [], [], [], []
    for i, n in enumerate(fixtures.NOTES):
        note_id, quiz_id = uuid.uuid4(), uuid.uuid4()
        created = now - timedelta(days=30 - i * 6, hours=i)
        notes.append(
            {
                "id": note_id,
                "user_id": user_id,
                "topic_id": topic_ids[n["topic"]],
                "title": n["title"],
                "summary": n["summary"],
                "key_points": n["key_points"],
                "source_kind": n["source_kind"],
                "content_hash": f"demo-{i}",
                "created_at": created,
            }
        )
        for j, (front, back) in enumerate(n["flashcards"]):
            stage = rng.choice([0, 1, 1, 2, 3])
            due = (
                now - timedelta(hours=rng.randint(1, 30))
                if j % 2 == 0
                else now + timedelta(days=rng.randint(1, 12))
            )
            cards.append(
                {
                    "user_id": user_id,
                    "topic_id": topic_ids[n["topic"]],
                    "note_id": note_id,
                    "front": front,
                    "back": back,
                    "origin": "ai",
                    "stage": stage,
                    "reviews": stage + rng.randint(0, 2),
                    "due_at": due,
                    "created_at": created + timedelta(seconds=j),
                }
            )
        quizzes.append(
            {
                "id": quiz_id,
                "user_id": user_id,
                "topic_id": topic_ids[n["topic"]],
                "note_id": note_id,
                "title": f"Quiz: {n['title']}",
                "questions": n["quiz"],
                "cache_key": f"note:{note_id}",
                "attempts": 2,
                "best_score": 4,
                "created_at": created,
            }
        )
        for k, score in enumerate((3, 4)):
            attempts.append(
                {
                    "user_id": user_id,
                    "quiz_id": quiz_id,
                    "score": score,
                    "total": 5,
                    "created_at": now - timedelta(days=11 - k * 5 - i),
                }
            )
    tq = fixtures.TOPIC_QUIZ
    quizzes.append(
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "topic_id": topic_ids[tq["topic"]],
            "note_id": None,
            "title": tq["title"],
            "questions": tq["questions"],
            "cache_key": tq["cache_key"],
            "attempts": 0,
            "best_score": None,
            "created_at": now - timedelta(days=3),
        }
    )
    await db.execute(insert(Note), notes)
    await db.execute(insert(Flashcard), cards)
    await db.execute(insert(Quiz), quizzes)
    await db.execute(insert(QuizAttempt), attempts)

    # Coach reports for the last 3 finished weeks, built from the real seeded stats.
    last_week, _ = week_bounds(now.date())
    reports = []
    for w in range(3):
        week_start: date = last_week - timedelta(days=7 * w)
        stats = await week_stats(db, user_id, DEMO_TZ, week_start)
        if stats["total_minutes"] == 0:
            continue
        summary, report = _coach_report(stats)
        reports.append(
            {
                "user_id": user_id,
                "week_start": week_start,
                "summary": summary,
                "report": report,
            }
        )
    if reports:
        await db.execute(insert(CoachReport), reports)

    await db.commit()
    return {
        "sessions": len(sessions),
        "notes": len(notes),
        "flashcards": len(cards),
        "coach_reports": len(reports),
    }


async def _main() -> None:
    from app.db import SessionLocal, engine

    demo_id = get_settings().demo_user_id
    if demo_id is None:
        raise SystemExit("Set DEMO_USER_ID (the Supabase user id of the demo account) first.")
    async with SessionLocal() as db:
        print(await reset_demo(db, demo_id))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
