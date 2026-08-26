from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from arq.connections import RedisSettings

from app.config import get_settings
from app.core.downloader import download_media
from app.core.metadata import probe_metadata
from app.core.uploader import smart_send
from app.services.job_service import create_job, update_job_status
from app.services.redis import incr_stat
from app.services.user_service import increment_download_count
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
    Includes Job tracking, stats, and cleanup.
    """
    get_settings()
    bot = ctx.get("bot")
    if not bot:
        raise RuntimeError("bot not in worker ctx - init worker with bot")

    # Create/Track job
    job = None
    try:
        job = await create_job(user_id, chat_id, url, quality)
        await incr_stat("jobs_created")
        await update_job_status(job.id, "downloading")
    except Exception as e:
        log.debug("job create failed in worker: %s", e)

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
        await bot.edit_message_text("⬇️ Downloading…", chat_id=chat_id, message_id=status_message_id)

        filepath = await asyncio.to_thread(download_media, url, quality, hook)  # type: ignore

        if job:
            try:
                await update_job_status(job.id, "uploading", file_path=str(filepath))
            except Exception:
                pass

        await bot.edit_message_text("⬆️ Uploading to Telegram…", chat_id=chat_id, message_id=status_message_id)

        caption = format_caption(meta, quality_label=quality)
        await smart_send(bot, chat_id, filepath, caption, meta)

        await bot.edit_message_text("✅ Done! File delivered above ☝️", chat_id=chat_id, message_id=status_message_id)
        if job:
            try:
                await update_job_status(job.id, "done")
            except Exception:
                pass
        try:
            await increment_download_count(user_id)
            await incr_stat("downloads_success")
        except Exception:
            pass
        return {"status": "done", "file": str(filepath) if filepath else ""}

    except Exception as e:
        log.exception("arq download_job failed url=%s quality=%s", url, quality)
        if job:
            try:
                await update_job_status(job.id, "failed", error=str(e)[:2000])
                await incr_stat("downloads_failed")
            except Exception:
                pass
        try:
            await bot.edit_message_text(f"❌ Failed: <code>{e}</code>", chat_id=chat_id, message_id=status_message_id)
        except Exception:
            pass
        raise
    finally:
        if filepath and filepath.exists():
            try:
                filepath.unlink()
                for ext in (".jpg", ".webp", ".png"):
                    p = filepath.with_suffix(ext)
                    if p.exists():
                        p.unlink(missing_ok=True)
            except Exception:
                pass


async def _edit_progress(bot, chat_id: int, msg_id: int, d: dict, url: str):
    import html

    from app.utils.format import human_bytes

    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    downloaded = d.get("downloaded_bytes") or 0
    pct = f"{downloaded/total*100:.1f}%" if total else (d.get("_percent_str") or "?%")
    speed = d.get("_speed_str") or (human_bytes(d.get("speed")) + "/s" if d.get("speed") else "—")
    eta = d.get("_eta_str") or (f"{int(d['eta'])}s" if d.get("eta") else "—")
    text = (
        f"⬇️ <b>Downloading… {pct}</b>\n{human_bytes(downloaded)} / {human_bytes(total)} • {speed} • ETA {eta}\n"
        f"<code>{html.escape(url[:80])}</code>"
    )
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
        from app.services.db import init_db

        ctx["bot"] = create_bot()
        try:
            await init_db()
        except Exception as e:
            log.warning("worker db init failed: %s", e)
        log.info("arq worker startup - bot created, db ready")

    async def shutdown(self, ctx: dict):
        bot = ctx.get("bot")
        if bot:
            await bot.session.close()
            log.info("arq worker shutdown - bot closed")
        # close redis pool if any
        try:
            from app.services.redis import close_redis

            await close_redis()
        except Exception:
            pass
