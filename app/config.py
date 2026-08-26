from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # Telegram
    bot_token: str = Field(..., alias="BOT_TOKEN")
    bot_api_id: int | None = Field(default=None, alias="BOT_API_ID")
    bot_api_hash: str | None = Field(default=None, alias="BOT_API_HASH")
    use_local_bot_api: bool = Field(default=False, alias="USE_LOCAL_BOT_API")
    local_bot_api_url: str = Field(default="http://telegram-bot-api:8081", alias="LOCAL_BOT_API_URL")

    # Webhook
    use_webhook: bool = Field(default=False, alias="USE_WEBHOOK")
    webhook_url: str | None = Field(default=None, alias="WEBHOOK_URL")
    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")
    webapp_host: str = Field(default="0.0.0.0", alias="WEBAPP_HOST")
    webapp_port: int = Field(default=8080, alias="WEBAPP_PORT")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    # Enable queue (arq) - set true in docker-compose where workers run; false for simple polling dev
    use_queue: bool = Field(default=False, alias="USE_QUEUE")

    # DB
    database_url: str = Field(default="sqlite+aiosqlite:///./data/bot.db", alias="DATABASE_URL")

    # Storage fallback
    use_r2_fallback: bool = Field(default=False, alias="USE_R2_FALLBACK")
    r2_endpoint: str | None = Field(default=None, alias="R2_ENDPOINT")
    r2_access_key_id: str | None = Field(default=None, alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str | None = Field(default=None, alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str | None = Field(default=None, alias="R2_BUCKET")
    r2_public_url: str | None = Field(default=None, alias="R2_PUBLIC_URL")
    r2_expire_seconds: int = Field(default=3600, alias="R2_EXPIRE_SECONDS")

    # App
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    max_concurrent_downloads: int = Field(default=3, alias="MAX_CONCURRENT_DOWNLOADS")
    max_downloads_per_user_per_hour: int = Field(default=20, alias="MAX_DOWNLOADS_PER_USER_PER_HOUR")
    download_dir: Path = Field(default=Path("./data/downloads"), alias="DOWNLOAD_DIR")
    cookies_dir: Path = Field(default=Path("./data/cookies"), alias="COOKIES_DIR")
    tmp_cleanup_seconds: int = Field(default=600, alias="TMP_CLEANUP_SECONDS")
    allow_playlist: bool = Field(default=False, alias="ALLOW_PLAYLIST")
    playlist_max_items: int = Field(default=5, alias="PLAYLIST_MAX_ITEMS")
    # Reliability
    auto_downgrade_large_files: bool = Field(default=True, alias="AUTO_DOWNGRADE_LARGE_FILES")
    large_file_threshold_bytes: int = Field(default=1900 * 1024 * 1024, alias="LARGE_FILE_THRESHOLD_BYTES")

    # Phase-4: Admin / monetize
    admin_user_ids: list[int] = Field(default_factory=list, alias="ADMIN_USER_IDS")
    donate_url: str | None = Field(default=None, alias="DONATE_URL")
    donate_text: str = Field(default="❤️ Support UniMedia — keep the fastest downloader alive!", alias="DONATE_TEXT")

    # Phase-5: Observability & advanced
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")
    enable_subtitles: bool = Field(default=False, alias="ENABLE_SUBTITLES")
    subtitle_langs: str = Field(default="en", alias="SUBTITLE_LANGS")  # comma sep e.g. "en,hi"
    ytdlp_proxy: str | None = Field(default=None, alias="YTDLP_PROXY")
    ytdlp_auto_update: bool = Field(default=True, alias="YTDLP_AUTO_UPDATE")  # Phase-5 fix: always keep yt-dlp fresh
    ytdlp_auto_update_interval_hours: int = Field(default=6, alias="YTDLP_AUTO_UPDATE_INTERVAL_HOURS")

    # Phase-5.1: Insane speed tuning (yt-dlp native + aria2c chunking)
    use_aria2: bool = Field(default=True, alias="USE_ARIA2")
    fast_mode: bool = Field(default=True, alias="FAST_MODE")  # skip thumbnail embedding for max speed
    probe_cache_ttl: int = Field(default=900, alias="PROBE_CACHE_TTL")  # 15 min
    aria2c_max_connections: int = Field(default=16, alias="ARIA2C_MAX_CONNECTIONS")

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if v is None or v == "" or v == []:
            return []
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            import json

            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                try:
                    return [int(x) for x in json.loads(v)]
                except Exception:
                    pass
            # comma or space separated
            parts = v.replace(";", ",").replace(" ", ",").split(",")
            return [int(p) for p in parts if p.strip().isdigit() or p.strip().lstrip("-").isdigit()]
        return v

    @field_validator("download_dir", "cookies_dir", mode="before")
    @classmethod
    def _to_path(cls, v):
        return Path(v) if isinstance(v, str) else v

    def ensure_dirs(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        (self.download_dir / ".gitkeep").touch(exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # allow BOT_TOKEN via env var without .env file in tests
    s = Settings()  # type: ignore[call-arg]
    s.ensure_dirs()
    return s
