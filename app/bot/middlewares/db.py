from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.user_service import upsert_user

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Upsert user on every interaction and inject `user` and `db_user` into data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user:
            try:
                db_user = await upsert_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                )
                data["db_user"] = db_user
            except Exception as e:
                log.debug("upsert_user failed: %s", e)
        return await handler(event, data)
