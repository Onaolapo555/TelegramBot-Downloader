from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.keyboards.inline import settings_keyboard
from app.utils.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, lang: str = "en"):
    await message.answer(t("start", lang))


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str = "en"):
    await message.answer(t("help", lang))


@router.message(Command("settings"))
async def cmd_settings(message: Message, lang: str = "en"):
    # TODO: load current pref from DB
    await message.answer(t("settings", lang), reply_markup=settings_keyboard("best"))


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    # TODO: real stats from DB/Redis
    await message.answer("📊 <b>Stats</b>\n\nWorker: online\nUptime: —\nDownloads today: —")


@router.callback_query(lambda c: c.data and c.data.startswith("set:"))
async def cb_set(callback, lang: str = "en"):
    val = callback.data.split(":", 1)[1]
    # TODO: persist to DB
    await callback.answer(f"Default set to {val}")
    await callback.message.edit_text(f"✅ Default quality set to <b>{val}</b>", reply_markup=settings_keyboard(val))
