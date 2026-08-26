from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.user_service import get_user_lang

log = logging.getLogger(__name__)

# Supported language codes
SUPPORTED_LANGS = {"en", "hi", "es", "fr", "de", "ar", "pt", "ru", "tr", "id"}


class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        lang = "en"
        user = getattr(event, "from_user", None)
        if user:
            # Try to load from DB, fallback to telegram language_code
            try:
                lang = await get_user_lang(user.id)
                # If user not in DB yet, respect telegram language if supported
                if lang == "en" and getattr(user, "language_code", None):
                    tg_lang = (user.language_code or "en")[:2].lower()
                    if tg_lang in SUPPORTED_LANGS:
                        # Don't override DB, but use tg_lang for first-time users
                        # DB will return "en" for unknown user, so we can use tg_lang
                        # Check if user exists: if download_count? we already fetched, but user may not exist
                        # We treat absence as use tg_lang if it differs from en
                        if tg_lang != "en":
                            lang = tg_lang
            except Exception as e:
                log.debug("i18n load failed: %s", e)
                # fallback to telegram code
                if getattr(user, "language_code", None):
                    tg_lang = (user.language_code or "en")[:2].lower()
                    if tg_lang in SUPPORTED_LANGS:
                        lang = tg_lang
        data["lang"] = lang
        return await handler(event, data)
