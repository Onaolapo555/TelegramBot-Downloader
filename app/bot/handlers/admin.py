from __future__ import annotations

import html

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings
from app.services.job_service import count_jobs, recent_jobs
from app.services.redis import get_stat
from app.services.user_service import count_users, total_downloads

router = Router()


def _is_admin(user_id: int) -> bool:
    s = get_settings()
    return user_id in (s.admin_user_ids or [])


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("❌ Admin only.")
        return

    s = get_settings()
    users = await count_users()
    downloads = await total_downloads()
    jobs_total = await count_jobs()
    jobs_done = await count_jobs("done")
    jobs_failed = await count_jobs("failed")
    redis_success = await get_stat("downloads_success")
    queued = await count_jobs("queued")

    text = (
        "🛠 <b>Admin Panel</b>\n\n"
        f"👥 Users: <b>{users:,}</b>\n"
        f"⬇️ Downloads (DB): <b>{downloads:,}</b>\n"
        f"📦 Jobs total: <b>{jobs_total:,}</b> (✅ {jobs_done:,} / ❌ {jobs_failed:,} / ⏳ {queued:,})\n"
        f"⚡️ Redis success: <b>{redis_success:,}</b>\n"
        f"🔧 Workers: <code>{s.max_concurrent_downloads} concurrent</code>\n"
        f"📁 Cleanup: <code>{s.tmp_cleanup_seconds}s</code>\n"
        f"📋 Playlist: <code>{s.allow_playlist} (max {s.playlist_max_items})</code>\n"
        f"💾 R2: <code>{'enabled' if s.use_r2_fallback else 'disabled'}</code>\n"
        f"🌐 Local API: <code>{'enabled' if s.use_local_bot_api else 'disabled'}</code>\n\n"
        "Commands:\n"
        "<code>/broadcast &lt;text&gt;</code> — send to all users (admin)\n"
        "<code>/stats</code> — public stats\n"
        "<code>/donate</code> — donate info"
    )
    await message.answer(text)

    # Recent jobs preview
    try:
        recents = await recent_jobs(limit=5)
        if recents:
            lines = ["\n📝 Recent jobs:"]
            for j in recents:
                lines.append(f"#{j.id} {j.quality} {j.status} — <code>{html.escape(j.url[:50])}</code>")
            await message.answer("\n".join(lines))
    except Exception:
        pass


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("❌ Admin only. Set <code>ADMIN_USER_IDS=your_id</code> in .env")
        return
    # Extract broadcast text
    text = message.text or ""
    # Format: /broadcast your message here
    payload = text.split(" ", 1)
    if len(payload) < 2 or not payload[1].strip():
        await message.answer("Usage: <code>/broadcast Your message here</code>\nWill send to all users in DB.")
        return
    broadcast_text = payload[1].strip()
    # Load all user ids
    from app.services.db import get_session_factory
    from app.models.user import User
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(User.id))
        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        await message.answer("No users to broadcast to.")
        return
    await message.answer(f"📢 Broadcasting to <b>{len(user_ids):,}</b> users…\n\n<code>{html.escape(broadcast_text[:300])}</code>")

    # Send in batches with throttling to avoid FloodWait
    import asyncio

    bot = message.bot
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 <b>Broadcast</b>\n\n{broadcast_text}")
            sent += 1
            await asyncio.sleep(0.05)  # 20 msg/s max
        except Exception:
            failed += 1
        # Optional: add delay every 100 to respect rate limit
        if sent % 100 == 0:
            await asyncio.sleep(1)
        if sent % 500 == 0:
            try:
                await bot.send_message(message.chat.id, f"Progress: {sent}/{len(user_ids)} sent, {failed} failed")
            except Exception:
                pass
    await message.answer(f"✅ Broadcast done: <b>{sent}</b> sent, <b>{failed}</b> failed.")


@router.message(Command("donate"))
async def cmd_donate(message: Message, lang: str = "en"):
    s = get_settings()
    # Multi-lang donate fallback to env text
    if s.donate_url:
        text = (
            f"{s.donate_text}\n\n"
            f"💳 <a href=\"{s.donate_url}\">Donate / Support</a>\n\n"
            "<i>Your support keeps the bot fast, ad-free, and 2GB-capable.</i>"
        )
        await message.answer(text, disable_web_page_preview=True)
    else:
        await message.answer(
            "❤️ <b>Support UniMedia</b>\n\n"
            "If you love the fastest downloader, consider supporting hosting / R2 / Local Bot API costs.\n"
            "Configure <code>DONATE_URL</code> in <code>.env</code> to enable donate button.\n\n"
            "<i>Example: DONATE_URL=https://buymeacoffee.com/yourname</i>"
        )
