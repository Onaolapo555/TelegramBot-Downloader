from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards.inline import cache_url_async, quality_keyboard
from app.core.downloader import estimate_filesize
from app.core.metadata import probe_metadata
from app.services.user_service import get_user_quality, upsert_user
from app.utils.format import human_bytes, human_duration
from app.utils.i18n import t
from app.utils.url import extract_urls

router = Router()


def _friendly_probe_error(e: Exception) -> str:
    msg = str(e).lower()
    raw = html.escape(str(e)[:450])
    # YouTube bot check — most common, handle first with friendly non-technical message
    try:
        from app.core.downloader import is_youtube_bot_error, youtube_bot_help_text

        if is_youtube_bot_error(e):
            return youtube_bot_help_text()
    except Exception:
        pass
    try:
        from app.core.downloader import is_cookie_encoding_error, youtube_cookie_help_text

        if is_cookie_encoding_error(e):
            return youtube_cookie_help_text()
    except Exception:
        pass
    try:
        from app.core.downloader import is_youtube_reload_error, youtube_reload_help_text

        if is_youtube_reload_error(e):
            return youtube_reload_help_text()
    except Exception:
        pass
    try:
        from app.core.downloader import is_format_not_available_error, youtube_format_help_text

        if is_format_not_available_error(e):
            return youtube_format_help_text()
    except Exception:
        pass
    if "youtube" in msg and ("sign in to confirm" in msg or "not a bot" in msg or "use --cookies" in msg):
        try:
            from app.core.downloader import youtube_bot_help_text

            return youtube_bot_help_text()
        except Exception:
            pass
        return (
            "❌ <b>YouTube is blocking downloads</b> (Sign in to confirm you’re not a bot)\n\n"
            "YouTube requires cookies. Add <code>data/cookies/youtube.txt</code> or set <code>YOUTUBE_COOKIES</code> env var.\n"
            "Other sites still work."
        )
    if "the page needs to be reloaded" in msg:
        try:
            from app.core.downloader import youtube_reload_help_text

            return youtube_reload_help_text()
        except Exception:
            pass
    if "latin-1" in msg and "can't encode" in msg:
        try:
            from app.core.downloader import youtube_cookie_help_text

            return youtube_cookie_help_text()
        except Exception:
            pass
    if "requested format is not available" in msg:
        try:
            from app.core.downloader import youtube_format_help_text

            # Only YouTube shows this after our SABR-safe fallback; other sites rarely hit it
            if "youtube" in msg or "use --list-formats" in msg:
                return youtube_format_help_text()
        except Exception:
            pass
    if "twitter" in msg or "x.com" in msg:
        if "json" in msg or "parse" in msg or "update" in msg or "expecting value" in msg:
            return (
                "❌ <b>X/Twitter link failed</b>\n\n"
                "X/Twitter changed their API — yt-dlp needs update.\n"
                f"<code>{raw}</code>\n\n"
                "✅ Bot auto-updates every 6h (and on this error). Try again in 30s.\n"
                "💡 If tweet is private/deleted it will still fail. Admin can add <code>data/cookies/twitter.txt</code>."
            )
        return f"❌ X/Twitter error:\n<code>{raw}</code>\nSend as <code>https://x.com/user/status/ID</code>."
    if "instagram" in msg and ("login" in msg or "cookie" in msg):
        return f"❌ Instagram needs login:\n<code>{raw}</code>\nAdd <code>data/cookies/instagram.txt</code> (Netscape)."
    if "tiktok" in msg and "json" in msg:
        return f"❌ TikTok JSON error — extractor outdated.\n<code>{raw}</code>\nRetry in 1 min (auto-update running)."
    if "please report this issue on https://github.com/yt-dlp" in msg:
        clean = raw.split("please report")[0][:350]
        return f"❌ Site changed, extractor failed:\n<code>{html.escape(clean)}</code>\nBot will auto-update, retry shortly."
    return f"❌ Could not probe link:\n<code>{raw}</code>"


@router.message(F.text & ~F.text.startswith("/"))
async def handle_url(message: Message, lang: str = "en"):
    urls = extract_urls(message.text or "")
    if not urls:
        return

    if message.from_user:
        try:
            await upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        except Exception:
            pass

    url = urls[0]
    if len(urls) > 1:
        await message.answer(f"ℹ️ Detected {len(urls)} links — processing the first one. Send links one by one for best results.")

    probing = await message.answer(t("probing", lang))

    # Probe with cache + auto-retry on twitter/json outdated errors
    meta: dict | None = None
    last_err: Exception | None = None
    for attempt in range(2):  # first attempt + one retry after update
        try:
            meta = await asyncio.wait_for(probe_metadata(url), timeout=20)
            break
        except TimeoutError:
            await probing.edit_text("❌ Probe timed out. The site may be slow or blocking. Try again.")
            return
        except Exception as e:
            last_err = e
            err_low = str(e).lower()
            should_retry = attempt == 0 and any(k in err_low for k in ["twitter", "json", "parse", "update to the latest version", "expecting value"])
            if should_retry:
                try:
                    await probing.edit_text("⚠️ Extractor outdated — updating yt-dlp and retrying…")
                except Exception:
                    pass
                try:
                    import subprocess
                    import sys

                    # Try global then --user (for docker bot user permission)
                    for args in (
                        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q"],
                        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q", "--user"],
                    ):
                        res = subprocess.run(args, timeout=45, capture_output=True)
                        if res.returncode == 0:
                            break
                        err = res.stderr.decode().lower() if res.stderr else ""
                        if "permission" not in err and "could not install" not in err:
                            break
                    try:
                        from app.core.downloader import _PROBE_CACHE

                        _PROBE_CACHE.clear()
                    except Exception:
                        pass
                except Exception:
                    pass
                continue  # retry
            await probing.edit_text(_friendly_probe_error(e))
            return

    if meta is None:
        if last_err:
            await probing.edit_text(_friendly_probe_error(last_err))
        else:
            await probing.edit_text("❌ Could not probe link.")
        return

    title = html.escape((meta.get("title") or "Untitled")[:300])
    uploader = html.escape((meta.get("uploader") or meta.get("channel") or "Unknown")[:80])
    duration = human_duration(meta.get("duration"))

    await cache_url_async(url)

    default_q = "best"
    if message.from_user:
        try:
            default_q = await get_user_quality(message.from_user.id)
        except Exception:
            pass

    caption = f"🎯 <b>Choose quality</b>\n\n🎬 <b>{title}</b>\n👤 {uploader} • ⏱ {duration}\n"
    if meta.get("view_count"):
        caption += f"👁 {meta['view_count']:,}\n"
    if meta.get("extractor_key"):
        caption += f"🌐 {meta['extractor_key']}\n"
    try:
        est = estimate_filesize(meta, default_q)  # type: ignore
        if est:
            caption += f"📦 ~{human_bytes(est)} (est. {default_q})\n"
            if est > 1900 * 1024 * 1024:
                caption += "⚠️ Estimated >1.9GB — will auto-downgrade if needed or use R2 link.\n"
    except Exception:
        pass
    if meta.get("width") and meta.get("height"):
        caption += f"📐 {meta['width']}x{meta['height']}\n"
    caption += f"🔗 <code>{html.escape(url[:80])}</code>"
    if default_q != "best":
        caption += f"\n\n💡 Your default: <b>{default_q}</b> (tap ✅). Change in /settings"

    try:
        await probing.delete()
    except Exception:
        pass

    playlist_count = meta.get("playlist_count") or (len(meta.get("entries") or []) if "entries" in meta else 0)
    if playlist_count and playlist_count > 1:
        from app.config import get_settings

        s = get_settings()
        if not s.allow_playlist:
            caption += f"\n\n⚠️ Playlist detected ({playlist_count} items) — only the first will be processed."

    await message.answer(caption, reply_markup=quality_keyboard(url, default_quality=default_q))
