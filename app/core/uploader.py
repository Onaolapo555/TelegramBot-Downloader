from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import get_settings
from app.services.storage import upload_to_r2

log = logging.getLogger(__name__)

# Telegram limits: 50MB bot API cloud, 2GB with Local Bot API
TELEGRAM_CLOUD_LIMIT = 50 * 1024 * 1024
TELEGRAM_LOCAL_LIMIT = 2 * 1024 * 1024 * 1024  # ~2GB
# We warn above 1.9GB to account for overhead


async def smart_send(
    bot: Bot,
    chat_id: int,
    filepath: Path,
    caption: str,
    meta: dict | None = None,
) -> None:
    """
    Try to send file via Telegram. If file >2GB or send fails due to size,
    fallback to R2 signed URL if configured.
    """
    s = get_settings()
    size = filepath.stat().st_size
    log.info("smart_send chat=%s file=%s size=%s", chat_id, filepath.name, size)

    # Choose send method based on extension
    suffix = filepath.suffix.lower()
    is_audio = suffix in {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}
    is_video = suffix in {".mp4", ".mkv", ".webm", ".mov", ".avi"}

    # If size exceeds Local limit -> must use R2
    if size > TELEGRAM_LOCAL_LIMIT - 50 * 1024 * 1024:
        log.warning("File too large for Telegram even with Local API: %s", size)
        await _fallback_r2(bot, chat_id, filepath, caption)
        return

    # Try Telegram upload
    try:
        input_file = FSInputFile(filepath)

        # Use appropriate method for better preview
        if is_audio:
            # Send as audio with title
            title = (meta or {}).get("title", filepath.stem)[:64]
            performer = (meta or {}).get("uploader", "")[:64] if meta else ""
            duration = int((meta or {}).get("duration") or 0) if meta else 0
            await bot.send_audio(
                chat_id,
                audio=input_file,
                caption=caption,
                title=title,
                performer=performer or None,
                duration=duration or None,
            )
        elif is_video:
            # send_video supports streaming + thumbnail
            duration = int((meta or {}).get("duration") or 0) if meta else 0
            width = (meta or {}).get("width")
            height = (meta or {}).get("height")
            await bot.send_video(
                chat_id,
                video=input_file,
                caption=caption,
                duration=duration or None,
                width=width,
                height=height,
                supports_streaming=True,
            )
        else:
            await bot.send_document(chat_id, document=input_file, caption=caption)

        log.info("Telegram send succeeded chat=%s file=%s", chat_id, filepath.name)
        return

    except Exception as e:
        err_str = str(e).lower()
        log.warning("Telegram send failed chat=%s err=%s", chat_id, e)

        # Detect size-related errors
        size_error_hints = ("too large", "file too big", "entity too large", "413", "request entity too large")
        if any(h in err_str for h in size_error_hints):
            await _fallback_r2(bot, chat_id, filepath, caption, original_error=e)
            return
        # For other errors, try fallback if R2 enabled, otherwise re-raise
        if s.use_r2_fallback:
            try:
                await _fallback_r2(bot, chat_id, filepath, caption, original_error=e)
                return
            except Exception as r2e:
                log.exception("R2 fallback also failed")
                raise e from r2e
        raise


async def _fallback_r2(bot: Bot, chat_id: int, filepath: Path, caption: str, original_error: Exception | None = None):
    s = get_settings()
    if not s.use_r2_fallback or not s.r2_bucket:
        # No fallback configured -> inform user
        msg = (
            "❌ File is too large to send via Telegram (even with Local Bot API).\n"
            f"Size: {filepath.stat().st_size / (1024*1024):.1f} MB\n"
            "Configure R2/S3 fallback or try a lower quality (720p/480p)."
        )
        if original_error:
            msg += f"\n<code>{original_error}</code>"
        await bot.send_message(chat_id, msg)
        return

    await bot.send_message(chat_id, "📦 File too large for Telegram — uploading to temporary cloud storage…")

    url = await upload_to_r2(filepath)

    # Send link + caption
    text = (
        f"{caption}\n\n"
        f"📥 <b>Download Link (expires in {s.r2_expire_seconds//3600}h):</b>\n"
        f'<a href="{url}">{url}</a>\n\n'
        f"💡 If link expires, send the URL again and pick a lower quality."
    )
    await bot.send_message(chat_id, text, disable_web_page_preview=True)
