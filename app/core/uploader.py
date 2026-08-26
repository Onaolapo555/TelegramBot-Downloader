from __future__ import annotations

import logging
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


def _detect_media_type(filepath: Path, meta: dict | None) -> str:
    suffix = filepath.suffix.lower()
    if suffix in {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}:
        return "audio"
    if suffix in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
        return "video"
    # fallback via meta
    if meta and meta.get("vcodec") != "none":
        return "video"
    return "document"


def _find_thumbnail(filepath: Path) -> Path | None:
    """Find companion thumbnail written by yt-dlp (same basename with .jpg/.webp)."""
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        cand = filepath.with_suffix(ext)
        if cand.exists():
            return cand
        # yt-dlp may write <id>.jpg not <job>_.mp4.jpg — also check download dir with same id prefix
        # Already handled via glob in downloader; but check anyway
    # also check for .jpg with same stem
    for p in filepath.parent.glob(filepath.stem + ".*"):
        if p.suffix.lower() in {".jpg", ".webp", ".png", ".jpeg"} and p != filepath:
            return p
    return None


async def smart_send(
    bot: Bot,
    chat_id: int,
    filepath: Path,
    caption: str,
    meta: dict | None = None,
) -> None:
    """
    Try to send file via Telegram. Supports:
    - 2GB via Local Bot API (if configured)
    - Automatic thumbnail for video
    - Fallback to R2 signed URL if file >2GB or Telegram returns size error
    """
    s = get_settings()
    size = filepath.stat().st_size
    log.info("smart_send chat=%s file=%s size=%s local_api=%s", chat_id, filepath.name, size, s.use_local_bot_api)

    # Choose send method based on extension
    suffix = filepath.suffix.lower()
    is_audio = suffix in {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}
    is_video = suffix in {".mp4", ".mkv", ".webm", ".mov", ".avi"}

    # If size exceeds Local limit -> must use R2
    # Keep 50MB headroom for Telegram overhead (api adds json wrapping)
    if size > TELEGRAM_LOCAL_LIMIT - 50 * 1024 * 1024:
        log.warning("File too large for Telegram even with Local API: %s", size)
        await _fallback_r2(bot, chat_id, filepath, caption, original_error=Exception(f"size {size} > 2GB"))
        return

    # Warn if cloud limit (50MB) and local API disabled — will likely fail, try anyway then R2
    if not s.use_local_bot_api and size > TELEGRAM_CLOUD_LIMIT:
        log.info("File >50MB but Local API disabled — Telegram cloud mode may reject (%s). Will try then fallback.", size)
        # Notify user proactively that big file attempt may take longer
        try:
            await bot.send_message(chat_id, f"📦 File is {size/(1024*1024):.1f} MB (>50MB). Uploading — if it fails, try lower quality or enable Local Bot API for 2GB support.")
        except Exception:
            pass

    # Try to locate thumbnail for video
    thumb_path = _find_thumbnail(filepath) if is_video else None
    thumb_file = None
    if thumb_path and thumb_path.stat().st_size < 10 * 1024 * 1024:  # Telegram thumb limit 320kb? but keep <10MB
        try:
            thumb_file = FSInputFile(thumb_path)
        except Exception:
            thumb_file = None

    # Try Telegram upload
    try:
        input_file = FSInputFile(filepath)

        # Use appropriate method for better preview
        if is_audio:
            # Send as audio with title + thumb
            title = (meta or {}).get("title", filepath.stem)[:64]
            performer = (meta or {}).get("uploader", "")[:64] if meta else ""
            duration = int((meta or {}).get("duration") or 0) if meta else 0
            # Send thumb if available for audio
            kwargs = {}
            if thumb_file:
                kwargs["thumbnail"] = thumb_file
            await bot.send_audio(
                chat_id,
                audio=input_file,
                caption=caption,
                title=title,
                performer=performer or None,
                duration=duration or None,
                **kwargs,
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
                thumbnail=thumb_file,
            )
        else:
            await bot.send_document(chat_id, document=input_file, caption=caption, thumbnail=thumb_file)

        log.info("Telegram send succeeded chat=%s file=%s thumb=%s", chat_id, filepath.name, bool(thumb_file))
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
        # No fallback configured -> inform user with actionable advice
        msg = (
            "❌ File is too large to send via Telegram (even with Local Bot API — limit ~2GB).\n"
            f"📦 Size: {filepath.stat().st_size / (1024*1024):.1f} MB\n"
            "💡 Try a lower quality (720p → 480p → 360p or Audio) or enable R2/S3 fallback.\n"
            "Configure in <code>.env</code>: <code>USE_R2_FALLBACK=true</code> + <code>R2_*</code> (Cloudflare R2 / S3 / B2).\n"
            "Then bot will upload and give you a temporary link (1h–24h)."
        )
        if original_error:
            msg += f"\n<code>{str(original_error)[:400]}</code>"
        await bot.send_message(chat_id, msg)
        return

    status_msg = await bot.send_message(chat_id, "📦 File too large for Telegram — uploading to temporary cloud storage…")

    try:
        url = await upload_to_r2(filepath)
    except Exception as e:
        log.exception("R2 upload failed")
        await status_msg.edit_text(f"❌ R2 upload failed: <code>{e}</code>\nTry lower quality (480p/360p).")
        return

    # Send link + caption
    hours = s.r2_expire_seconds // 3600
    hours_str = f"{hours}h" if hours else f"{s.r2_expire_seconds//60}m"
    text = (
        f"{caption}\n\n"
        f"📥 <b>Download Link (expires in {hours_str}):</b>\n"
        f'<a href="{url}">{url}</a>\n\n'
        f"💡 If link expires, send the URL again and pick a lower quality."
    )
    try:
        await status_msg.delete()
    except Exception:
        pass
    await bot.send_message(chat_id, text, disable_web_page_preview=True)
