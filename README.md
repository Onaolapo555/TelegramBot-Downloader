# UniMedia Downloader Bot

Fastest, most reliable Telegram bot for downloading videos/audio from YouTube, TikTok, Instagram, Twitter/X, Facebook, Reddit, Vimeo, Twitch, etc. powered by `yt-dlp` + `aiogram 3` + `Local Bot API` (2GB files).

## Quick Start (Local Dev - Polling)

```bash
# 1. Install deps (requires Python 3.11+ and ffmpeg)
pip install -U pip
pip install -e .  # or: pip install -r requirements (if using pip)
# Or with uv (recommended):
# uv sync

# 2. FFmpeg required
# Windows: winget install Gyan.FFmpeg
# macOS: brew install ffmpeg
# Linux: sudo apt install ffmpeg

# 3. Env
cp .env.example .env
# edit .env -> set BOT_TOKEN

# 4. Run (polling, no Redis/DB needed for MVP)
python -m app.main --polling

# With Docker (full stack: bot + worker + redis + postgres + Local Bot API)
docker compose -f docker/docker-compose.yml up --build
# With Local Bot API for 2GB (Phase-3):
docker compose --profile local-api -f docker/docker-compose.yml up --build
```

## Production Deploy (Webhook + Local Bot API)

1. Get `API_ID` / `API_HASH` from https://my.telegram.org
2. Set `USE_LOCAL_BOT_API=true` and `USE_WEBHOOK=true` in `.env`
3. Point `WEBHOOK_URL` to your domain, expose `8080` via Nginx + TLS
4. `docker compose -f docker/docker-compose.yml up -d --build`
5. For 2GB: `docker compose --profile local-api -f docker/docker-compose.yml up -d --build` (needs `BOT_API_ID/HASH`)

See `docker/docker-compose.yml` comments for Local Bot API setup (enables 2GB uploads).

## Phase-3 (Large File & Speed) — New

- `concurrent_fragment_downloads=16`, `skip_unavailable_fragments`, `extractor_retries=3` for max speed/reliability (`app/core/downloader.py:47`)
- Auto-downgrade >1.9GB estimate to smaller quality (config `AUTO_DOWNGRADE_LARGE_FILES`, `LARGE_FILE_THRESHOLD_BYTES`) to stay under Telegram 2GB
- Thumbnail embed (`writethumbnail` + `EmbedThumbnail`) + `smart_send` finds companion `.jpg/.webp` (`app/core/uploader.py:23`)
- `smart_send` warns if `>50MB` without Local API, tries video/audio/document with thumbnail, falls back to R2 presigned URL (`app/services/storage.py:26`) when `USE_R2_FALLBACK=true`
- Docker `telegram-bot-api` under `profiles: [local-api]` (`docker/docker-compose.yml:75`)

## Architecture

```
Telegram -> [aiogram Bot (webhook/polling)] -> Redis (arq queue) -> Workers (yt-dlp + FFmpeg)
                                              -> Postgres (users/prefs)
                                              -> Local Bot API (2GB) -> fallback R2/S3 signed URL
```

- `app/bot/` - stateless handlers, keyboards, middlewares
- `app/core/` - downloader, metadata probe, uploader, cleanup
- `app/workers/` - arq tasks + progress throttling
- `app/services/` - db, R2 storage

## Commands

- `/start` - welcome + prefs
- `/help` - how to use
- `/settings` - default quality / audio
- `/language` - change language
- `/stats` - bot stats
- `/admin` - admin panel (requires `ADMIN_USER_IDS`)
- `/donate` - support link
- `/broadcast` - admin broadcast (with `ADMIN_USER_IDS`)

## Phase-5 (Observability & Ops) — New

- Metrics: `ENABLE_METRICS=true` exposes `/metrics` (Prometheus `text/plain` or JSON), `/health`/`/healthz` even in polling mode (`app/main.py:177`), includes `users`, `jobs_total/done/failed/queued`, `redis_success/failed`, `disk_free_mb` (`app/main.py:202`). Protect `/metrics` via Nginx `allow 127.0.0.1; deny all;` `docker/nginx.conf.example:19`.
- Sentry: `SENTRY_DSN` optional (`app/main.py:35` via `sentry-sdk` if installed, `traces_sample_rate=0.1`), structured `structlog` JSON in webhook mode.
- `yt-dlp` auto-update: `YTDLP_AUTO_UPDATE=true` runs `pip install -U yt-dlp` on `on_startup` (`app/main.py:74`), version logged.
- Per-domain cookies `data/cookies/{youtube,instagram,tiktok,twitter,facebook}.txt` + generic `cookies.txt` (`app/core/downloader.py:90` `_resolve_cookiefile`), proxy `YTDLP_PROXY=http://proxy:8080` (`app/core/downloader.py:167`), subtitles `ENABLE_SUBTITLES=true` + `SUBTITLE_LANGS=en,hi` (`writesubtitles/writeautomaticsub`).
- Webhook Nginx TLS example `docker/nginx.conf.example:1`, `Makefile` `docker-local`/`docker-scale`/`docker-logs`.

## Notes

- R2 fallback auto-uploads files >2GB and returns expiring link
- Files are deleted from `data/downloads` after 10min (configurable)
- `yt-dlp` is updated on container start if `YTDLP_AUTO_UPDATE=true`; mount `data/cookies` for IG/TikTok if needed (per-domain supported)
- Cookies: put `youtube.txt` etc in `data/cookies/` — see `app/core/downloader.py:90` and `app/core/metadata.py:25`
