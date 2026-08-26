from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.inline import language_keyboard, settings_keyboard
from app.services.job_service import count_jobs, total_jobs
from app.services.redis import get_stat
from app.services.user_service import (
    count_users,
    get_user_quality,
    set_user_lang,
    set_user_quality,
    total_downloads,
    upsert_user,
)
from app.utils.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, lang: str = "en"):
    # Persist user
    if message.from_user:
        try:
            await upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name, lang)
        except Exception:
            pass
    await message.answer(t("start", lang))
    # Also show quick settings hint
    # Don't spam: just start message is enough


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str = "en"):
    await message.answer(t("help", lang))


@router.message(Command("settings"))
async def cmd_settings(message: Message, lang: str = "en"):
    current = "best"
    if message.from_user:
        try:
            current = await get_user_quality(message.from_user.id)
        except Exception:
            pass
    # Determine user lang for keyboard highlight
    user_lang = lang
    await message.answer(
        t("settings", lang) + f"\n\nCurrent: <b>{current}</b>",
        reply_markup=settings_keyboard(current),
    )
    # Follow-up: language selection
    await message.answer("🌐 Choose language:", reply_markup=language_keyboard(user_lang))


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    # Real stats from DB + Redis
    try:
        users = await count_users()
        downloads = await total_downloads()
        jobs_total = await total_jobs()
        jobs_done = await count_jobs("done")
        jobs_failed = await count_jobs("failed")
        redis_success = await get_stat("downloads_success")
        # Prefer Redis if present, else DB total
        text = (
            "📊 <b>Bot Stats</b>\n\n"
            f"👥 Users: <b>{users:,}</b>\n"
            f"⬇️ Downloads (DB): <b>{downloads:,}</b>\n"
            f"📦 Jobs: <b>{jobs_total:,}</b> (✅ {jobs_done:,} / ❌ {jobs_failed:,})\n"
            f"⚡️ Successful sends (Redis): <b>{redis_success:,}</b>\n"
            f"🔧 Worker: <code>online</code>"
        )
    except Exception as e:
        text = f"📊 <b>Stats</b>\n\nWorker: online\nError fetching DB stats: <code>{e}</code>"
    await message.answer(text)


@router.callback_query(F.data.startswith("set:"))
async def cb_set(callback: CallbackQuery, lang: str = "en"):
    val = callback.data.split(":", 1)[1]  # type: ignore
    try:
        await set_user_quality(callback.from_user.id, val)
    except Exception:
        pass
    # Use translation if available, else fallback
    saved_msg = t("settings_saved", lang, val=val)
    if saved_msg == "settings_saved":
        saved_msg = f"Default set to {val}"
    await callback.answer(saved_msg)
    try:
        await callback.message.edit_text(f"✅ Default quality set to <b>{val}</b>", reply_markup=settings_keyboard(val))  # type: ignore
    except Exception:
        pass


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(callback: CallbackQuery, lang: str = "en"):
    new_lang = callback.data.split(":", 1)[1]  # type: ignore
    try:
        await set_user_lang(callback.from_user.id, new_lang)
    except Exception:
        pass
    await callback.answer(f"Language → {new_lang}")
    try:
        await callback.message.edit_text(f"✅ Language set to <b>{new_lang}</b>", reply_markup=language_keyboard(new_lang))  # type: ignore
        # also send translated start to confirm
        await callback.message.answer(t("start", new_lang))  # type: ignore
    except Exception:
        pass


@router.message(Command("language"))
@router.message(Command("lang"))
async def cmd_language(message: Message, lang: str = "en"):
    await message.answer("🌐 Choose language:", reply_markup=language_keyboard(lang))
