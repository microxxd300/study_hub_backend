# Study Hub API

FastAPI backend for **Study Hub**, an AI-powered focus and study tracker (installable PWA). Async Python 3.12, Pydantic v2, SQLAlchemy 2.0 + asyncpg on Supabase Postgres, deployed to Render's free tier with Docker.

> Frontend (React PWA), screenshots and the architecture diagram live in the frontend repo.

## Stack

| | |
| --- | --- |
| API | FastAPI (async), Pydantic v2 (`extra="forbid"` on every input), GZip, SSE via `StreamingResponse` |
| Database | Supabase Postgres via the **session pooler** (IPv4); SQLAlchemy 2.0 async + asyncpg; Alembic. The transaction pooler (port 6543) also works, since prepared-statement caching is disabled automatically. |
| Auth | Supabase JWTs verified on every request against the project **JWKS** (cached in memory), with HS256 fallback for legacy projects. Bearer tokens only, no cookies, so no CSRF surface. |
| Rate limits | slowapi + `limits` with **Upstash Redis** storage |
| AI | `app/ai/provider.py`: **Gemini Flash** primary (multimodal, streamed), **Groq** fallback on 429/5xx/invalid output. Output is always validated by a Pydantic schema. |
| Email | Resend batch API |
| Jobs | cron-job.org → `GET /health` (keep-alive) and `POST /internal/daily` (`X-Cron-Secret`) |

## API

All `/api` routes need `Authorization: Bearer <supabase access token>`. Errors always look like `{"error": {"code": "...", "message": "..."}}`. Lists are cursor-paginated: `?limit=20&cursor=…` → `{items, next_cursor}` (default 20, max 50).

| Method | Path | |
| --- | --- | --- |
| GET | `/health` | Keep-alive, no DB |
| GET / PATCH | `/api/me` | Bootstrap (creates the profile on first call; `X-Timezone` seeds the timezone) / update |
| GET / POST | `/api/topics` | List / create (max 30, names ≤ 40 chars, unique per user, case-insensitive) |
| PATCH | `/api/topics/{id}` | Rename, recolor, archive |
| POST | `/api/sessions` | Save a focus session (idempotent on its client-generated id) |
| GET | `/api/sessions` | History (cursor on `started_at, id`) |
| GET | `/api/dashboard` | Everything the home screen needs, in one request |
| GET | `/api/heatmap?topic_id=` | 365-day heatmap + streak stats, optionally per topic |
| GET / DELETE | `/api/notes`, `/api/notes/{id}` | List (small columns only) / detail with cards + quizzes / delete |
| GET | `/api/flashcards`, `/api/flashcards/due`, `/api/flashcards/due/count` | All cards / due now (cursor on `due_at, id`) / badge count |
| POST | `/api/flashcards/{id}/review` | `{correct}` → SM-2-style ladder |
| GET | `/api/quizzes`, `/api/quizzes/{id}` | List / detail |
| POST | `/api/quizzes/generate` | **SSE.** From `note_id` or `topic_name`; cached |
| POST | `/api/quizzes/{id}/submit` | Grade; wrong answers become flashcards |
| POST | `/api/snap` | **SSE.** Multipart `file` (JPEG/PNG/WebP ≤ 4 MB, PDF ≤ 3 MB / 10 pages) or `text`, plus optional `topic_id` |
| POST | `/api/command/parse` | Regex first, AI fallback (cached) |
| POST | `/api/coach/generate` | **SSE.** Weekly coach for the last finished week (cached; no AI when the week is empty) |
| POST | `/internal/daily` | Cron: keep-alive, demo reset, streak emails (`X-Cron-Secret`) |

SSE events: `status`, `delta` (raw model output as it streams), `restart` (fallback provider took over), `result` (validated + saved payload) and `error`. Cache hits return a single `result` event, so clients have one code path.

## Performance

**N+1 prevention**
- Every relationship is `lazy="raise"`: an accidental lazy load is an exception, not a silent query. Related rows load explicitly (`selectinload`) or lists select only the columns they need.
- A dev-only SQLAlchemy `before_cursor_execute` listener counts queries per request (`QueryCountMiddleware`). It exposes `X-Query-Count` and logs a warning above 5.
- Tests assert query budgets: **dashboard ≤ 5**, **sessions list ≤ 1**, **flashcards list / due ≤ 1**, **heatmap ≤ 4**, **note detail ≤ 3**. Each is independent of data volume.

**One-request dashboard.** `GET /api/dashboard` runs five small queries **sequentially on one `AsyncSession`** (it isn't safe for concurrent use): profile → topics → heatmap rows → one stats statement (current/longest streak, totals, due cards, last week's minutes) → latest coach report.

**Aggregation in SQL, never Python loops.**
- `daily_summary` (unique `(user_id, date)`) is **upserted** when a session is saved: `total_minutes`, `session_count` and `minutes_by_topic jsonb` incremented in place. The heatmap is one indexed read of ≤ 365 rows.
- Streaks use gaps-and-islands (`date - row_number()` grouped). Topic-filtered streaks read one key from the jsonb.
- Weekly coach stats, per-topic minutes (`jsonb_each_text … GROUP BY`) and the streak-email candidate list (a `LATERAL` streak count over every user) are each a single statement.

**Keyset pagination** everywhere: `WHERE (sort_col, id) < (:ts, :id) ORDER BY sort_col DESC, id DESC LIMIT n+1`, with an opaque base64 cursor. Page 500 costs the same as page 1.

**Indexes**: `study_sessions (user_id, started_at DESC)` · `daily_summary UNIQUE (user_id, date)` · `flashcards (user_id, due_at)`, `(user_id, created_at DESC)`, `(note_id)` · `notes (user_id, created_at DESC)` + `UNIQUE (user_id, content_hash)` · `topics (user_id)` + `UNIQUE (user_id, lower(name))` · `quizzes (user_id, created_at DESC)`, `(cache_key)`, `UNIQUE (user_id, cache_key)`.

**Other:** the async pool is sized for the free tier (`pool_size=5, max_overflow=5`, pre-ping). Lists never return large text fields. SSE endpoints release their pooled connection before the slow AI call. GZip skips `text/event-stream`. All middleware is pure ASGI, so streams are never buffered.

## Free-tier rules

- **One DB write per timer run**: the timer is client-side. A session save is three round trips (validate in one statement, idempotent insert, summary upsert).
- **Every AI result is cached**: notes by content hash, quizzes by `note:<id>` or `topic:<normalised name>` (topic quizzes are shared across users, so no repeated AI calls), coach reports per user and week, command parses in `ai_cache`. Identical requests never regenerate.
- **Uploads are never stored.** Bytes go to Gemini and are dropped; only extracted text and study aids are saved.
- Output tokens are capped per call, and per-user AI usage (calls + tokens) is logged in `ai_usage`.

## Security

- **CORS**: only `FRONTEND_ORIGIN` + `http://localhost:5173`; methods `GET/POST/PATCH/DELETE`; headers `Authorization, Content-Type, X-Timezone`.
- **Rate limits**: 100 req / 15 min per IP globally (except `/health`, `/internal`). AI is 5/min and 20/day per user; Snap & Study 5/day per user; the shared demo account gets 3 AI calls/day **per visitor IP**. Checks run before consuming, so a rejected call never burns quota. Every 429 has a `Retry-After` header and a friendly message. The client IP comes from the proxy-appended `X-Forwarded-For` hop (`TRUSTED_PROXY_HOPS`), not the spoofable left-most value.
- **No IDOR**: every query filters on the authenticated `user_id`. Foreign ids (topic, session id reuse) are checked for ownership, and there are tests for each.
- **RLS** is enabled on every table with no policies, and `anon`/`authenticated` grants are revoked, so Supabase's public Data API can't touch the data. The API connects as the table owner.
- **Validation**: Pydantic `extra="forbid"` and max lengths on every string. Sessions are checked server-side: timezone-aware timestamps, nothing in the future, no overlaps, max 12 h. Uploads are sniffed by magic bytes. User content in emails is HTML-escaped, and prompts fence user content as data.
- **Headers**: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`; in production also HSTS and `CSP: default-src 'none'`. `/docs` and `/openapi.json` are disabled in production. `/internal/*` requires `X-Cron-Secret` (constant-time compare).
- The demo account is read-only for shared settings (topics, profile, deletions).

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements-dev.txt
docker compose up -d db                             # Postgres on :54329 (+ studyhub_test db)
cp .env.example .env                                # set DATABASE_URL to the local one
alembic upgrade head
uvicorn app.main:app --reload                       # http://localhost:8000/docs
```

### Tests

```bash
pytest            # 38 tests against a real Postgres (jsonb, ON CONFLICT and window functions need one)
ruff check . && ruff format --check .
```

The suite runs the real Alembic migration and covers: auth and the error envelope, CORS, validation, IDOR, session rules and idempotency, daily_summary upserts, streak maths, keyset pagination, query-count budgets, `lazy="raise"`, Snap & Study (with the AI mocked) and its cache, Gemini → Groq fallback, invalid AI JSON, topic-quiz cache sharing, quiz → flashcards, the SRS ladder, AI and global rate limits with `Retry-After`, coach caching and the no-activity path, the regex command parser, `/internal/daily` auth, the demo seed and its guards, and streak-email selection. Set `TEST_DATABASE_URL` to use another database.

## Deploy

1. **Supabase**: create a project.
   - *Connect → Session pooler* → copy the URI into `DATABASE_URL`, changing the scheme to `postgresql+asyncpg://`.
   - *Authentication → Providers*: enable Email and Google (Google Cloud OAuth client with the Supabase callback URL). Under *URL configuration*, add `https://<frontend>/auth/callback`.
   - `SUPABASE_URL=https://<ref>.supabase.co` (JWKS is fetched from `/auth/v1/.well-known/jwks.json`).
2. **Upstash**: create a Redis database and use its `rediss://` URL as `REDIS_URL`.
3. **AI keys**: `GEMINI_API_KEY` (Google AI Studio) and `GROQ_API_KEY` (optional fallback).
4. **Resend**: verify a sending domain, then set `RESEND_API_KEY` and `EMAIL_FROM`.
5. **Render**: *New → Blueprint* with this repo (`render.yaml`, Docker, free plan, Singapore region, health check `/health`). Fill in the env vars from `.env.example` and set `ENV=production` and `FRONTEND_ORIGIN=https://<frontend>`. Migrations run on boot (`alembic upgrade head`).
6. **Demo account**: in Supabase *Auth → Users*, add a user (e.g. `demo@studyhub.app`, password of your choice). Put its id in `DEMO_USER_ID`, run `python -m app.demo.seed` once (or wait for the daily job), and give the email/password to the frontend as `VITE_DEMO_*`.
7. **cron-job.org**:
   - `GET https://<api>/health` every **10 minutes** (keeps Render warm).
   - `POST https://<api>/internal/daily` daily at **12:00 UTC** with header `X-Cron-Secret: <CRON_SECRET>`. It acts as the Supabase keep-alive, resets the demo, and sends streak-saver emails.

## Layout

```
app/
  main.py          app factory, middleware order (CORS outermost), routers
  config.py        settings (env vars)
  db.py            async engine/pool, dev-only query counter
  models.py        SQLAlchemy models (lazy="raise" everywhere)
  schemas.py       Pydantic request/response + AI output schemas
  auth.py          Supabase JWT verification (JWKS cache)
  rate_limit.py    global ASGI limiter + per-user AI quotas
  middleware.py    security headers, query-count guard (pure ASGI)
  errors.py        {error: {code, message}} envelope
  pagination.py    keyset cursor helpers
  sse.py           Server-Sent Events helpers
  ai/              provider (Gemini → Groq), prompts, usage logging
  services/        sessions, stats (streak SQL), coach, srs, command parser, reminders, email, profiles
  routers/         one module per resource
  demo/            fixtures + seed/reset
alembic/           migrations (0001 includes RLS)
tests/             pytest suite (real Postgres)
```

## Notes and deviations from the original brief

- **Tables**: besides the brief's nine tables there is `quiz_attempts` (the weekly coach needs quiz stats over time) and `ai_cache` (caches command-bar parses, so *every* AI result is cached).
- **Global rate limiting** is a small ASGI middleware over slowapi's limiter and storage. slowapi's own middleware resolves endpoints by scanning `app.routes`, which misses routes inside FastAPI's included routers, so every route would have counted as exempt.
- **Weeks** for the coach run Monday–Sunday in the user's timezone. The streak email is sent once daily at 12:00 UTC, using each user's local "today".
- **Buddy mode** (`profiles.buddy_mode`: `on` | `quiet` | `off`, migration `0002`) stores the frontend mascot preference; `PATCH /api/me {"buddy_mode": …}`. The shared demo profile is read-only, so demo visitors keep it in localStorage.
- **Spaced repetition**: correct answers climb 1 → 3 → 7 → 14 days (then stay at 14). A miss resets to stage 0 and brings the card back in 10 minutes. "Due" is evaluated at read time; nothing runs in the background.
