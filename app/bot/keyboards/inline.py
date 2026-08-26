from __future__ import annotations

import hashlib

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def quality_keyboard(url: str, has_audio_only: bool = True, default_quality: str | None = None) -> InlineKeyboardMarkup:
    """Build quality selector. Highlights default_quality with ✅."""
    h = _hash_url(url)
    kb = InlineKeyboardBuilder()

    # Map quality keys to labels
    options = [
        ("best", "🏆 Best (auto)"),
        ("1080", "🎬 1080p"),
        ("720", "🎬 720p"),
        ("480", "🎬 480p"),
        ("360", "🎬 360p"),
    ]
    audio_opts = [
        ("audio_mp3", "🎵 Audio MP3"),
        ("audio_m4a", "🎵 Audio M4A"),
    ]

    for key, label in options:
        prefix = "✅ " if default_quality == key else ""
        kb.button(text=f"{prefix}{label}", callback_data=f"dl:{key}:{h}")

    if has_audio_only:
        for key, label in audio_opts:
            prefix = "✅ " if default_quality == key else ""
            kb.button(text=f"{prefix}{label}", callback_data=f"dl:{key}:{h}")

    kb.button(text="❌ Cancel", callback_data=f"dl:cancel:{h}")
    # layout: 2 per row for video, 2 for audio, 1 for cancel
    if has_audio_only:
        kb.adjust(2, 2, 2, 1)
    else:
        kb.adjust(2, 2, 1)
    return kb.as_markup()


def language_keyboard(current: str = "en") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    langs = [
        ("en", "🇬🇧 English"),
        ("hi", "🇮🇳 हिंदी"),
        ("es", "🇪🇸 Español"),
        ("fr", "🇫🇷 Français"),
        ("de", "🇩🇪 Deutsch"),
        ("ar", "🇸🇦 العربية"),
        ("pt", "🇵🇹 Português"),
        ("ru", "🇷🇺 Русский"),
    ]
    for code, label in langs:
        prefix = "✅ " if code == current else ""
        kb.button(text=f"{prefix}{label}", callback_data=f"lang:{code}")
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


# ---------------------------------------------------------------------------
# URL cache - now Redis-backed with in-memory fallback (via redis service)
# Keep local in-memory for immediate reads before async redis write completes
# ---------------------------------------------------------------------------
_URL_CACHE: dict[str, str] = {}


def cache_url(url: str) -> str:
    h = _hash_url(url)
    _URL_CACHE[h] = url
    # Also push to Redis asynchronously if possible (fire-and-forget)
    try:
        import asyncio

        from app.services.redis import cache_url_redis

        # schedule cache to redis if loop running
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(cache_url_redis(url, h))
        except RuntimeError:
            # no loop - ignore, will be cached on next explicit async call
            pass
    except Exception:
        pass
    return h


def get_cached_url(h: str) -> str | None:
    # sync version - only checks memory (used as fallback, async version preferred)
    return _URL_CACHE.get(h)


async def get_cached_url_async(h: str) -> str | None:
    # Prefer Redis, fallback to memory
    try:
        from app.services.redis import get_cached_url_redis

        redis_val = await get_cached_url_redis(h)
        if redis_val:
            return redis_val
    except Exception:
        pass
    return _URL_CACHE.get(h)


async def cache_url_async(url: str) -> str:
    """Async version that guarantees Redis caching."""
    h = _hash_url(url)
    _URL_CACHE[h] = url
    try:
        from app.services.redis import cache_url_redis

        await cache_url_redis(url, h)
    except Exception:
        pass
    return h
