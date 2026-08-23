from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from arq import ArqRedis
from arq.connections import RedisSettings

from app.config import get_settings
from app.core.downloader import Quality, download_media
from app.core.uploader import smart_send
from app.core.metadata import probe_metadata
from app.utils.format import format_caption

log = logging.getLogger(__name__)


async def download_job(
    ctx: dict,
    url: str,
    quality: str,
    chat_id: int,
    status_message_id: int,
    user_id: int,
) -> dict:
    """
    arq task: download url with quality and send to chat_id.
    Runs in separate worker process. Requires bot instance via ctx['bot'].
    """
    s = get_settings()
    bot = ctx.get("bot")
    if not bot:
        raise RuntimeError("bot not in worker ctx - init worker with bot")

    # progress editing helper
    last_edit = 0.0
    loop = asyncio.get_running_loop()

    def hook(d: dict):
        nonlocal last_edit
        if d.get("status") != "downloading":
            return
        import time
        now = time.monotonic()
        if now - last_edit < 1.8:
            return
        last_edit = now
        # schedule edit
        asyncio.run_coroutine_threadsafe(
            _edit_progress(bot, chat_id, status_message_id, d, url), loop
        )

    # probe meta for caption
    try:
        meta = await probe_metadata(url)
    except Exception:
        meta = {"webpage_url": url, "title": "Media"}

    filepath: Path | None = None
    try:
        # update status
        await bot.edit_message_text("⬇️ Downloading…", chat_id=chat_id, message_id=status_message_id)

        filepath = await asyncio.to_thread(download_media, url, quality, hook)  # type: ignore

        await bot.edit_message_text("⬆️ Uploading to Telegram…", chat_id=chat_id, message_id=status_message_id)

        caption = format_caption(meta, quality_label=quality)
        await smart_send(bot, chat_id, filepath, caption, meta)

        await bot.edit_message_text("✅ Done! File delivered above ☝️", chat_id=chat_id, message_id=status_message_id)
        return {"status": "done", "file": str(filepath)}

    except Exception as e:
        log.exception("arq download_job failed url=%s quality=%s", url, quality)
        try:
            await bot.edit_message_text(f"❌ Failed: <code>{e}</code>", chat_id=chat_id, message_id=status_message_id)
        except Exception:
            pass
        raise
    finally:
        if filepath and filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass


async def _edit_progress(bot, chat_id: int, msg_id: int, d: dict, url: str):
    from app.utils.format import human_bytes

    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    downloaded = d.get("downloaded_bytes") or 0
    pct = f"{downloaded/total*100:.1f}%" if total else (d.get("_percent_str") or "?%")
    speed = d.get("_speed_str") or (human_bytes(d.get("speed")) + "/s" if d.get("speed") else "—")
    eta = d.get("_eta_str") or (f"{int(d['eta'])}s" if d.get("eta") else "—")
    text = f"⬇️ <b>Downloading… {pct}</b>\n{human_bytes(downloaded)} / {human_bytes(total)} • {speed} • ETA {eta}"
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


# --- arq WorkerSettings ---

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)  # type: ignore
    functions = [download_job]
    max_jobs = get_settings().max_concurrent_downloads * 2
    job_timeout = 3600  # 1h per download (for big files)

    async def startup(self, ctx: dict):
        # Create bot for worker process
        from app.bot import create_bot
        ctx["bot"] = create_bot()
        log.info("arq worker startup - bot created")

    async def shutdown(self, ctx: dict):
        bot = ctx.get("bot")
        if bot:
            await bot.session.close()
            log.info("arq worker shutdown - bot closed")
