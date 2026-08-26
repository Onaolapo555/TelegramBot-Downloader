from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards.inline import cache_url_async, quality_keyboard
from app.core.metadata import probe_metadata
from app.services.user_service import get_user_quality, upsert_user
from app.utils.format import human_duration
from app.utils.i18n import t
from app.utils.url import extract_urls

router = Router()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_url(message: Message, lang: str = "en"):
    urls = extract_urls(message.text or "")
    if not urls:
        return

    # Upsert user silently (ensure preferences & stats tracking)
    if message.from_user:
        try:
            await upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        except Exception:
            pass

    url = urls[0]
    if len(urls) > 1:
        await message.answer(f"ℹ️ Detected {len(urls)} links — processing the first one. Send links one by one for best results.")

    probing = await message.answer(t("probing", lang))

    try:
        meta = await asyncio.wait_for(probe_metadata(url), timeout=20)
    except TimeoutError:
        await probing.edit_text("❌ Probe timed out. The site may be slow or blocking. Try again.")
        return
    except Exception as e:
        await probing.edit_text(f"❌ Could not probe link:\n<code>{html.escape(str(e)[:800])}</code>")
        return

    title = html.escape((meta.get("title") or "Untitled")[:300])
    uploader = html.escape((meta.get("uploader") or meta.get("channel") or "Unknown")[:80])
    duration = human_duration(meta.get("duration"))

    # cache URL (Redis + memory) for callback retrieval
    await cache_url_async(url)

    # Load user's default quality to highlight in keyboard
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
    caption += f"🔗 <code>{html.escape(url[:80])}</code>"
    if default_q != "best":
        caption += f"\n\n💡 Your default: <b>{default_q}</b> (tap ✅). Change in /settings"

    try:
        await probing.delete()
    except Exception:
        pass

    # Optional: if playlist detected and not allowed, inform
    playlist_count = meta.get("playlist_count") or (len(meta.get("entries") or []) if "entries" in meta else 0)
    if playlist_count and playlist_count > 1:
        from app.config import get_settings

        s = get_settings()
        if not s.allow_playlist:
            caption += f"\n\n⚠️ Playlist detected ({playlist_count} items) — only the first will be processed. Enable playlists in settings or send single-video links."

    await message.answer(caption, reply_markup=quality_keyboard(url, default_quality=default_q))
