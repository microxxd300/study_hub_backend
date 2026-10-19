from functools import lru_cache
from urllib.parse import quote
from uuid import UUID

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | production | test

    # Database: Supabase SESSION pooler (port 5432) is IPv4-friendly.
    # Format: postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/studyhub"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Supabase auth
    supabase_url: str = ""  # https://<ref>.supabase.co
    supabase_jwt_secret: str = ""  # only for legacy HS256 projects; JWKS is used otherwise
    supabase_jwt_audience: str = "authenticated"

    # CORS
    frontend_origin: str = "http://localhost:5173"

    # Rate limiting (Upstash: rediss://default:<token>@<host>:6379)
    redis_url: str = "memory://"
    rate_limit_enabled: bool = True  # set false only for local load/UI testing
    trusted_proxy_hops: int = 1  # Render adds one X-Forwarded-For hop; 0 = ignore the header

    # AI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_vision_model: str = "qwen/qwen3.8-27b"
    ai_max_output_tokens: int = 4096
    ai_timeout_seconds: float = 60.0

    # Email
    resend_api_key: str = ""
    email_from: str = "Study Hub <streaks@example.com>"

    # Internal cron endpoints
    cron_secret: str = ""

    # Demo account
    demo_user_id: UUID | None = None

    # Dev-only N+1 guard
    query_warn_threshold: int = 5

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        # Supabase hands out postgresql:// (or postgres://) URIs; we need the async driver.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                v = "postgresql+asyncpg://" + v[len(prefix) :]
                break
        # A raw "@" (or ":" etc.) in the password breaks URL parsing: the last "@"
        # is the real separator, so percent-encode the password before it.
        scheme, sep, rest = v.partition("://")
        if sep and rest.count("@") > 1:
            creds, _, host = rest.rpartition("@")
            user, _, password = creds.partition(":")
            v = f"{scheme}://{user}:{quote(password, safe='')}@{host}"
        return v

    @field_validator("supabase_url")
    @classmethod
    def _supabase_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not v.startswith("https://") and not v.startswith("http://"):
            raise ValueError("SUPABASE_URL must be the project URL (https://<ref>.supabase.co), not a key")
        return v

    @field_validator("demo_user_id", mode="before")
    @classmethod
    def _empty_demo_id(cls, v: object) -> object:
        return v or None

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origins(self) -> list[str]:
        origins = {o.strip().rstrip("/") for o in self.frontend_origin.split(",") if o.strip()}
        origins.add("http://localhost:5173")
        return sorted(origins)

    @property
    def app_url(self) -> str:
        return self.frontend_origin.split(",")[0].strip().rstrip("/")

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
