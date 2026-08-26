from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.keyboards.inline import get_cached_url_async
from app.config import get_settings
from app.core.downloader import Quality, download_media
from app.core.metadata import probe_metadata
from app.core.uploader import smart_send
from app.services.job_service import create_job, update_job_status
from app.services.redis import enqueue_download, incr_stat
from app.services.user_service import increment_download_count
from app.utils.format import format_caption, human_bytes

router = Router()
log = logging.getLogger(__name__)

# Map callback suffix to Quality
QUALITY_MAP: dict[str, Quality] = {
    "best": "best",
    "1080": "1080",
    "720": "720",
    "480": "480",
    "360": "360",
    "audio_mp3": "audio_mp3",
    "audio_m4a": "audio_m4a",
}


@router.callback_query(F.data.startswith("dl:"))
async def cb_download(callback: CallbackQuery, lang: str = "en"):
    # dl:<quality>:<hash>
    try:
        _, qual_key, h = callback.data.split(":", 2)  # type: ignore
    except ValueError:
        await callback.answer("Invalid request", show_alert=True)
        return

    if qual_key == "cancel":
        await callback.answer("Cancelled")
        try:
            await callback.message.edit_text("❌ Cancelled.")
        except Exception:
            pass
        return

    url = await get_cached_url_async(h)
    # also try sync fallback
    if not url:
        from app.bot.keyboards.inline import get_cached_url as sync_get

        url = sync_get(h)
    if not url:
        await callback.answer("Link expired — please send the URL again.", show_alert=True)
        return

    quality: Quality = QUALITY_MAP.get(qual_key, "best")  # type: ignore

    await callback.answer(f"Downloading {qual_key}…")

    # Create job record for tracking / stats
    job = None
    try:
        job = await create_job(callback.from_user.id, callback.message.chat.id, url, qual_key)  # type: ignore
        await incr_stat("jobs_created")
    except Exception as e:
        log.debug("create_job failed: %s", e)

    # Edit message to show queue/preparing status
    try:
        await callback.message.edit_text(  # type: ignore
            f"⬇️ Downloading <b>{qual_key}</b>…\n<code>{html.escape(url[:120])}</code>\n\n<i>Preparing…</i>"
        )
    except Exception:
        pass

    # Try enqueue to Redis worker if queue enabled; otherwise fallback to direct download
    s = get_settings()
    status_msg_id = callback.message.message_id  # type: ignore
    chat_id = callback.message.chat.id  # type: ignore

    enqueued = False
    if s.use_queue:
        try:
            enqueued = await asyncio.wait_for(
                enqueue_download(url, quality, chat_id, status_msg_id, callback.from_user.id),
                timeout=3.0,
            )
        except TimeoutError:
            log.warning("enqueue timeout, fallback to direct")
            enqueued = False
        except Exception as e:
            log.debug("enqueue check failed: %s", e)
            enqueued = False

        if enqueued:
            try:
                await callback.message.edit_text(  # type: ignore
                    f"⏳ <b>Queued</b> ({qual_key})\n<code>{html.escape(url[:80])}</code>\n\nWorker will process shortly. You'll receive the file here."
                )
            except Exception:
                pass
            if job:
                try:
                    await update_job_status(job.id, "queued")
                except Exception:
                    pass
            return

    # Fallback: direct download in background task (queue disabled or Redis down)
    asyncio.create_task(_do_download(callback, url, quality, job_id=job.id if job else None))


async def _do_download(callback: CallbackQuery, url: str, quality: Quality, job_id: int | None = None):
    get_settings()
    chat_id = callback.message.chat.id  # type: ignore
    bot = callback.bot

    # progress state
    status_msg = callback.message

    last_edit = 0.0
    loop = asyncio.get_running_loop()

    def progress_hook(d: dict):
        nonlocal last_edit
        # Called from yt-dlp thread -> schedule edit
        # d: {"status": "downloading", "downloaded_bytes": ..., "total_bytes": ..., "speed": ..., "eta": ...}
        if d.get("status") != "downloading":
            return
        now = loop.time()
        if now - last_edit < 1.8:
            return
        last_edit = now
        pct = d.get("_percent_str") or ""
        # yt-dlp may not give percent; compute
        if not pct and d.get("total_bytes"):
            pct = f"{d.get('downloaded_bytes', 0) / d['total_bytes'] * 100:.1f}%"
        speed = d.get("_speed_str") or (human_bytes(d["speed"]) + "/s" if d.get("speed") else "—")
        eta = d.get("_eta_str") or (f"{int(d['eta'])}s" if d.get("eta") else "—")
        downloaded = human_bytes(d.get("downloaded_bytes"))
        total = human_bytes(d.get("total_bytes") or d.get("total_bytes_estimate"))
        text = f"⬇️ <b>Downloading… {pct}</b>\n{downloaded} / {total} • {speed} • ETA {eta}\n<code>{html.escape(url[:80])}</code>"
        # schedule edit (fire and forget)
        asyncio.run_coroutine_threadsafe(_safe_edit(status_msg, text), loop)

    async def _safe_edit(msg, text: str):
        try:
            await msg.edit_text(text)
        except Exception:
            pass

    # Run yt-dlp probe for caption before download (for pretty caption)
    meta: dict = {}
    try:
        meta = await probe_metadata(url)
    except Exception:
        meta = {"webpage_url": url, "title": "Media"}

    filepath: Path | None = None
    try:
        if job_id:
            try:
                await update_job_status(job_id, "downloading")
            except Exception:
                pass
        # download (blocking -> run in thread)
        filepath = await asyncio.to_thread(download_media, url, quality, progress_hook)

        await _safe_edit(status_msg, "⬆️ <b>Uploading to Telegram…</b>")
        if job_id:
            try:
                await update_job_status(job_id, "uploading", file_path=str(filepath))
            except Exception:
                pass

        # Determine caption
        caption = format_caption(meta, quality_label=qual_key_label(quality))

        # Smart send: try Local API direct, fallback to R2
        await smart_send(bot, chat_id, filepath, caption, meta)

        await _safe_edit(status_msg, "✅ <b>Done!</b> File delivered above ☝️")
        if job_id:
            try:
                await update_job_status(job_id, "done")
            except Exception:
                pass
        try:
            await increment_download_count(callback.from_user.id)
            await incr_stat("downloads_success")
        except Exception:
            pass
        # auto delete status after 10s
        await asyncio.sleep(10)
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        log.exception("download failed %s %s", url, quality)
        err = html.escape(str(e)[:900])
        if job_id:
            try:
                await update_job_status(job_id, "failed", error=str(e)[:2000])
                await incr_stat("downloads_failed")
            except Exception:
                pass
        try:
            await status_msg.edit_text(f"❌ <b>Failed</b> ({html.escape(quality)}):\n<code>{err}</code>\n\nTry another quality or send link again.")
        except Exception:
            await bot.send_message(chat_id, f"❌ Failed: <code>{err}</code>")
    finally:
        # cleanup file
        if filepath and filepath.exists():
            try:
                filepath.unlink()
                # also try to remove companion .jpg/.webp thumbnails
                for ext in (".jpg", ".webp", ".png"):
                    p = filepath.with_suffix(ext)
                    if p.exists():
                        p.unlink()
            except Exception:
                pass


def qual_key_label(q: Quality) -> str:
    m = {"best": "Best", "1080": "1080p", "720": "720p", "480": "480p", "360": "360p", "audio_mp3": "Audio MP3", "audio_m4a": "Audio M4A"}
    return m.get(q, q)
