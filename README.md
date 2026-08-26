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
- `/stats` - bot stats

## Notes

- R2 fallback auto-uploads files >2GB and returns expiring link
- Files are deleted from `data/downloads` after 10min (configurable)
- `yt-dlp` is updated on container start; mount `data/cookies` for IG/TikTok if needed
