from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.config import get_settings
from app.services.redis import is_rate_limited, record_hit

log = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Redis-backed rate limiter with in-memory fallback. Checks are async."""

    def __init__(self, max_per_hour: int | None = None):
        s = get_settings()
        self.max_per_hour = max_per_hour or s.max_downloads_per_user_per_hour

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        # Admin bypass (Phase-4): admins never rate-limited
        try:
            if user.id in (get_settings().admin_user_ids or []):
                return await handler(event, data)
        except Exception:
            pass

        # Only limit download intents
        should_check = False
        if isinstance(event, Message) and event.text and ("http" in event.text or "youtu" in event.text):
            should_check = True
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith("dl:"):
            should_check = True

        if should_check:
            limited = await is_rate_limited(user.id, self.max_per_hour)
            if limited:
                if isinstance(event, CallbackQuery):
                    await event.answer(f"⏳ Too many requests. Wait {limited}s", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(f"⏳ Too many requests. Please wait {limited}s.")
                return None
            # need to record after check? is_rate_limited already increments via Redis.
            # For memory fallback we must record separately.
            # Redis version already counts the hit inside is_rate_limited.
            # For memory fallback, is_rate_limited returns None but hasn't recorded; we record now.
            # Detect fallback by trying to see if redis available: if redis unavailable, record_hit does memory.
            # We call record_hit to ensure memory path is persisted.
            # For redis path, record_hit is no-op (already counted).
            await record_hit(user.id)

        return await handler(event, data)
