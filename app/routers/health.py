from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict:
    # No DB call: this is pinged every 10 minutes to keep the Render instance warm.
    return {"status": "ok"}
