import html
import logging

import httpx

from app.config import get_settings

log = logging.getLogger("studyhub.email")

RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
BATCH_SIZE = 100  # Resend batch limit


def streak_email(to: str, name: str | None, streak: int, app_url: str) -> dict:
    who = html.escape(name or "there")  # user content is always escaped
    days = f"{streak} day{'s' if streak != 1 else ''}"
    body = f"""\
<div style="background:#000;color:#fff;padding:32px;font-family:Helvetica,Arial,sans-serif">
  <p style="font-family:monospace;letter-spacing:.08em;color:#8a8a8a;font-size:12px;margin:0">STREAK SAVER</p>
  <h1 style="font-size:40px;margin:16px 0 8px">{streak}</h1>
  <p style="font-family:monospace;letter-spacing:.08em;color:#8a8a8a;font-size:12px;margin:0">DAY STREAK</p>
  <p style="font-size:16px;line-height:1.5;margin:24px 0">Hey {who}, your {days} streak ends at midnight.
  A 15-minute focus session keeps it alive.</p>
  <a href="{html.escape(app_url)}/timer" style="display:inline-block;background:#fff;color:#000;
     padding:12px 24px;border-radius:999px;text-decoration:none;font-weight:600">Start a session</a>
  <p style="color:#8a8a8a;font-size:12px;margin-top:32px">Turn these off in Profile &rarr; Reminders.</p>
</div>"""
    return {
        "from": get_settings().email_from,
        "to": [to],
        "subject": f"Keep your {days} streak alive",
        "html": body,
        "text": f"Hey {name or 'there'}, your {days} streak ends at midnight. "
        f"A 15-minute session keeps it alive: {app_url}/timer",
    }


async def send_batch(messages: list[dict]) -> list[int]:
    """Send via Resend's batch API. Returns the indexes of accepted messages."""
    key = get_settings().resend_api_key
    if not key or not messages:
        if messages:
            log.info("RESEND_API_KEY not set; skipping %d emails", len(messages))
        return []
    sent: list[int] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for i in range(0, len(messages), BATCH_SIZE):
            chunk = messages[i : i + BATCH_SIZE]
            resp = await client.post(RESEND_BATCH_URL, headers={"Authorization": f"Bearer {key}"}, json=chunk)
            if resp.status_code >= 300:
                log.error("Resend batch failed: %s %s", resp.status_code, resp.text[:300])
                continue
            sent.extend(range(i, i + len(chunk)))
    return sent
