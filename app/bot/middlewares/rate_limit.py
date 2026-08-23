from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from app.config import get_settings


class RateLimitMiddleware(BaseMiddleware):
    """Simple in-memory rate limiter. Production: replace with Redis INCR+EXPIRE."""

    def __init__(self, max_per_hour: int | None = None):
        s = get_settings()
        self.max_per_hour = max_per_hour or s.max_downloads_per_user_per_hour
        self._hits: Dict[int, deque[float]] = defaultdict(deque)
        self._last_msg: Dict[int, float] = {}

    def _is_limited(self, user_id: int) -> int | None:
        now = time.time()
        dq = self._hits[user_id]
        # drop older than 1h
        while dq and now - dq[0] > 3600:
            dq.popleft()
        if len(dq) >= self.max_per_hour:
            # seconds until oldest expires
            return int(3600 - (now - dq[0])) + 1
        # anti-spam: 2s between messages
        last = self._last_msg.get(user_id, 0)
        if now - last < 2:
            return int(2 - (now - last)) + 1
        return None

    def _record(self, user_id: int):
        now = time.time()
        self._hits[user_id].append(now)
        self._last_msg[user_id] = now

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        limited = self._is_limited(user.id)
        if limited:
            # silently answer callback or reply
            if isinstance(event, CallbackQuery):
                await event.answer(f"⏳ Too many requests. Wait {limited}s", show_alert=True)
            elif isinstance(event, Message):
                await event.answer(f"⏳ Too many requests. Please wait {limited}s.")
            return None
        # record only for messages that are download intents (URL or callback dl:*)
        should_record = False
        if isinstance(event, Message) and event.text and ("http" in event.text or "youtu" in event.text):
            should_record = True
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith("dl:"):
            should_record = True
        if should_record:
            self._record(user.id)
        return await handler(event, data)
