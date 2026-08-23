from __future__ import annotations
# Placeholder for future i18n middleware that loads user language from DB.
# Currently defaults to "en" - can be extended to read from Postgres.

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class I18nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # TODO: load lang from DB via data["user_lang"]
        data["lang"] = "en"
        return await handler(event, data)
