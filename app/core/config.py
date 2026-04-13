from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "VM Lifecycle API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Backend selection ─────────────────────────────────────────────────────
    # Controls which VMRepository implementation is injected at runtime.
    #   mock      → in-memory dict, no credentials, no persistence (default)
    #   sqlite    → SQLite file, no server needed, survives restarts
    #   openstack → real OpenStack via openstacksdk (requires credentials)
    BACKEND: Literal["mock", "sqlite", "openstack"] = "mock"

    # ── Observability: logging ────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    # json → structured JSON lines (production / log aggregators like Loki, Datadog)
    # text → human-readable lines (local development)
    LOG_FORMAT: Literal["json", "text"] = "json"
    # When set, logs are ALSO written to this file in addition to stdout.
    # Leave empty to log to stdout only (recommended for containerised deployments).
    LOG_FILE: Optional[str] = None
    LOG_MAX_BYTES: int = 10 * 1024 * 1024   # 10 MB per file before rotation
    LOG_BACKUP_COUNT: int = 5               # keep 5 rotated files → max 50 MB on disk

    # ── Mock repository ───────────────────────────────────────────────────────
    # How many seconds before a newly created VM transitions BUILD → ACTIVE.
    MOCK_BUILD_DELAY_SECONDS: int = 10

    # ── SQLite repository ─────────────────────────────────────────────────────
    SQLITE_DB_PATH: str = "./data/vms.db"
    SQLITE_BUILD_DELAY_SECONDS: int = 10

    # ── OpenStack (used only when BACKEND=openstack) ──────────────────────────
    # Prefer clouds.yaml over these env vars — see clouds.yaml.example.
    OS_CLOUD: str = "devstack"
    OS_AUTH_URL: str = ""
    OS_USERNAME: str = ""
    OS_PASSWORD: str = ""
    OS_PROJECT_NAME: str = ""
    OS_USER_DOMAIN_NAME: str = "Default"
    OS_PROJECT_DOMAIN_NAME: str = "Default"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton. Override in tests via lru_cache.cache_clear()."""
    return Settings()
