from __future__ import annotations

import asyncio
import html

from aiogram import Router, F
from aiogram.types import Message

from app.bot.keyboards.inline import quality_keyboard, cache_url
from app.core.metadata import probe_metadata
from app.utils.format import human_duration
from app.utils.i18n import t
from app.utils.url import extract_urls

router = Router()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_url(message: Message, lang: str = "en"):
    urls = extract_urls(message.text or "")
    if not urls:
        # ignore non-URL chatter, but hint if seems like pasted link attempt
        if message.text and len(message.text) < 500:
            # only reply if user likely tried to send link
            pass
        return

    url = urls[0]  # handle first URL; TODO: playlist handling
    if len(urls) > 1:
        await message.answer(f"ℹ️ Detected {len(urls)} links — processing the first one. Send links one by one for best results.")

    probing = await message.answer(t("probing", lang))

    # Probe metadata async with timeout
    try:
        meta = await asyncio.wait_for(probe_metadata(url), timeout=20)
    except asyncio.TimeoutError:
        await probing.edit_text("❌ Probe timed out. The site may be slow or blocking. Try again.")
        return
    except Exception as e:
        await probing.edit_text(f"❌ Could not probe link:\n<code>{html.escape(str(e)[:800])}</code>")
        return

    title = html.escape((meta.get("title") or "Untitled")[:300])
    uploader = html.escape((meta.get("uploader") or meta.get("channel") or "Unknown")[:80])
    duration = human_duration(meta.get("duration"))

    h = cache_url(url)

    # Keep probing message and add keyboard - or replace it
    text = t("choose_quality", lang, title=title, duration=duration, uploader=uploader)
    # enriched text
    caption = f"🎯 <b>Choose quality</b>\n\n🎬 <b>{title}</b>\n👤 {uploader} • ⏱ {duration}\n"
    if meta.get("view_count"):
        caption += f"👁 {meta['view_count']:,}\n"
    if meta.get("extractor_key"):
        caption += f"🌐 {meta['extractor_key']}\n"
    caption += f"\n🔗 <code>{html.escape(url[:80])}</code>"

    # Try to send thumbnail if available
    thumb = meta.get("thumbnail")
    try:
        await probing.delete()
    except Exception:
        pass

    await message.answer(caption, reply_markup=quality_keyboard(url))
