from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from app.utils.url import extract_urls


class UrlFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        if not message.text:
            return False
        urls = extract_urls(message.text)
        if urls:
            return {"urls": urls}
        return False
