"""Deploy this API to Render from the terminal (no dashboard needed).

    .venv/Scripts/python scripts/render_deploy.py            # create or redeploy, then wait
    .venv/Scripts/python scripts/render_deploy.py --status   # just show the latest deploy

Reads RENDER_API_KEY and the app settings from .env. Secrets are sent to
Render over HTTPS but never printed.

What it does (each step is one Render REST API call):
  1. GET  /v1/owners                       -> your workspace id (ownerId)
  2. GET  /v1/services?name=...            -> does the service already exist?
  3. POST /v1/services                     -> create it (Docker, free, Singapore)
     or POST /v1/services/{id}/deploys     -> redeploy the latest commit
  4. GET  /v1/services/{id}/deploys        -> poll until the deploy is live
  5. GET  https://<service>.onrender.com/health
"""

import secrets
import sys
import time
from pathlib import Path

import httpx

API = "https://api.render.com/v1"
SERVICE_NAME = "study-hub-api"
REPO = "https://github.com/microxxd300/study_hub_backend"
BRANCH = "main"
REGION = "singapore"  # closest Render region to the Supabase DB (Tokyo)

# Env vars the app needs on Render (everything else uses the code defaults).
REQUIRED = ["DATABASE_URL", "SUPABASE_URL", "GEMINI_API_KEY", "FRONTEND_ORIGIN"]
OPTIONAL = ["GROQ_API_KEY", "RESEND_API_KEY", "EMAIL_FROM", "REDIS_URL", "CRON_SECRET", "DEMO_USER_ID"]


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def die(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def main() -> None:
    env = read_env(Path(__file__).resolve().parent.parent / ".env")
    key = env.get("RENDER_API_KEY")
    if not key:
        die("Add RENDER_API_KEY=... to .env (Render > Account Settings > API Keys).")

    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    client = httpx.Client(base_url=API, headers=headers, timeout=30)

    # 1. Workspace
    r = client.get("/owners", params={"limit": 20})
    if r.status_code == 401:
        die("Render rejected the API key (401). Create a new one and update .env.")
    r.raise_for_status()
    owners = [o["owner"] for o in r.json()]
    if not owners:
        die("No Render workspace found for this API key.")
    owner = owners[0]
    print(f"[1/5] Workspace: {owner['name']}")

    # 2. Existing service?
    r = client.get("/services", params={"name": SERVICE_NAME, "ownerId": owner["id"], "limit": 20})
    r.raise_for_status()
    existing = [s["service"] for s in r.json() if s["service"]["name"] == SERVICE_NAME]

    if "--status" in sys.argv:
        if not existing:
            die(f"No service named {SERVICE_NAME} yet.")
        show_status(client, existing[0])
        return

    if existing:
        service = existing[0]
        print(f"[2/5] Service exists ({service['serviceDetails'].get('url')}); triggering a redeploy")
        r = client.post(f"/services/{service['id']}/deploys", json={"clearCache": "do_not_clear"})
        r.raise_for_status()
    else:
        # 3. Create
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            die(f"Missing in .env: {', '.join(missing)}")
        env_vars = [{"key": "ENV", "value": "production"}]
        env_vars += [{"key": k, "value": env[k]} for k in REQUIRED + OPTIONAL if env.get(k)]
        if not env.get("CRON_SECRET"):
            env_vars.append({"key": "CRON_SECRET", "value": secrets.token_urlsafe(32)})
            print("      (generated a CRON_SECRET; copy it from Render > Environment later)")
        print(f"[2/5] Creating {SERVICE_NAME}: Docker, free plan, {REGION}, branch {BRANCH}")
        print(f"      env vars sent: {', '.join(v['key'] for v in env_vars)}  (values hidden)")
        body = {
            "type": "web_service",
            "name": SERVICE_NAME,
            "ownerId": owner["id"],
            "repo": REPO,
            "branch": BRANCH,
            "autoDeploy": "yes",
            "envVars": env_vars,
            "serviceDetails": {
                "runtime": "docker",
                "plan": "free",
                "region": REGION,
                "healthCheckPath": "/health",
                "envSpecificDetails": {"dockerfilePath": "./Dockerfile", "dockerContext": "."},
            },
        }
        r = client.post("/services", json=body)
        if r.status_code >= 400:
            die(f"Render refused to create the service: HTTP {r.status_code} {r.text[:300]}")
        service = r.json()["service"]
        print(f"[3/5] Created. URL: {service['serviceDetails'].get('url')}")

    # 4. Wait for the deploy
    wait_for_deploy(client, service)

    # 5. Health check
    url = service["serviceDetails"].get("url")
    try:
        h = httpx.get(f"{url}/health", timeout=60)
        print(f"[5/5] {url}/health -> HTTP {h.status_code} {h.text}")
    except httpx.HTTPError as exc:
        print(f"[5/5] Health check failed: {exc}")


def latest_deploy(client: httpx.Client, service: dict) -> dict | None:
    r = client.get(f"/services/{service['id']}/deploys", params={"limit": 1})
    r.raise_for_status()
    items = r.json()
    return items[0]["deploy"] if items else None


def wait_for_deploy(client: httpx.Client, service: dict) -> None:
    print("[4/5] Building (first build takes ~5-8 min). Status:", end=" ", flush=True)
    last = None
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        d = latest_deploy(client, service)
        status = d["status"] if d else "queued"
        if status != last:
            print(status, end=" ", flush=True)
            last = status
        if status == "live":
            print("\n      Deploy is live.")
            return
        if status in {"build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"}:
            print(f"\n      Deploy ended with '{status}'. Open the service's Logs in the dashboard,")
            print("      or paste them to Claude. Re-run this script after fixing.")
            sys.exit(1)
        time.sleep(10)
    print("\n      Still building after 20 min; check again with --status.")


def show_status(client: httpx.Client, service: dict) -> None:
    d = latest_deploy(client, service)
    print(f"{service['name']}: {service['serviceDetails'].get('url')}")
    commit = d["commit"]["id"][:7] if d and d.get("commit") else "-"
    print(f"latest deploy: {d['status'] if d else 'none'} (commit {commit})")


if __name__ == "__main__":
    main()
