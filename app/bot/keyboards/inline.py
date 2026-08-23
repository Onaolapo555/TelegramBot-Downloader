from __future__ import annotations

import hashlib
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def quality_keyboard(url: str, has_audio_only: bool = True) -> InlineKeyboardMarkup:
    h = _hash_url(url)
    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 Best (auto)", callback_data=f"dl:best:{h}")
    kb.button(text="🎬 1080p", callback_data=f"dl:1080:{h}")
    kb.button(text="🎬 720p", callback_data=f"dl:720:{h}")
    kb.button(text="🎬 480p", callback_data=f"dl:480:{h}")
    kb.button(text="🎬 360p", callback_data=f"dl:360:{h}")
    if has_audio_only:
        kb.button(text="🎵 Audio MP3", callback_data=f"dl:audio_mp3:{h}")
        kb.button(text="🎵 Audio M4A", callback_data=f"dl:audio_m4a:{h}")
    kb.button(text="❌ Cancel", callback_data=f"dl:cancel:{h}")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def settings_keyboard(current: str = "best") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    opts = [("best", "🏆 Best"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("audio_mp3", "🎵 MP3")]
    for val, label in opts:
        prefix = "✅ " if val == current else ""
        kb.button(text=f"{prefix}{label}", callback_data=f"set:{val}")
    kb.adjust(2, 3)
    return kb.as_markup()


# In-memory URL store for callback (stateless: we also encode metadata URL hash -> actual URL via Redis/cache)
# For MVP we store in memory dict with TTL via simple dict (production: Redis)
_URL_CACHE: dict[str, str] = {}


def cache_url(url: str) -> str:
    h = _hash_url(url)
    _URL_CACHE[h] = url
    return h


def get_cached_url(h: str) -> str | None:
    return _URL_CACHE.get(h)
