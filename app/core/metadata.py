from __future__ import annotations

import asyncio
from typing import Any

import yt_dlp

from app.config import get_settings


async def probe_metadata(url: str, quality: str | None = None) -> dict[str, Any]:
    """Fast metadata probe without downloading. Validates URL via yt-dlp extract_info. Cached for speed."""
    s = get_settings()
    # Phase-5.1: check in-memory + Redis probe cache (15 min TTL) for insane preparing speed
    try:
        from app.core.downloader import _get_probe_cache, _set_probe_cache

        cached = _get_probe_cache(url, quality)
        if cached:
            return cached
        # Also check Redis shared cache (covers bot->worker cross-process)
        from app.services.redis import get_cached_probe

        r_cached = await get_cached_probe(url)
        if r_cached:
            # Populate in-memory for next call
            try:
                _set_probe_cache(url, r_cached, quality)
            except Exception:
                pass
            return r_cached
    except Exception:
        pass

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not s.allow_playlist,
        "extract_flat": False,
        "skip_download": True,
        "socket_timeout": 12,  # reduced from 15 for faster fail
        "no_cache_dir": False,  # use yt-dlp cache for extractors
    }
    # Phase-5: per-domain cookies + proxy + playlist limit
    try:
        from app.core.downloader import _resolve_cookiefile

        cf = _resolve_cookiefile(url=url)
        if cf:
            opts["cookiefile"] = cf
    except Exception:
        pass
    if s.ytdlp_proxy:
        opts["proxy"] = s.ytdlp_proxy
    if s.allow_playlist:
        opts["playlistend"] = s.playlist_max_items

    def _probe():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # handle playlist: take first entry
            if info and "entries" in info:
                entries = list(info["entries"])
                if entries:
                    # filter None entries
                    first = next((e for e in entries if e), None)
                    if first:
                        # inherit playlist title if needed
                        first["playlist_title"] = info.get("title")
                        return first
            return info

    # timeout is handled by caller; run in thread to not block event loop
    info = await asyncio.to_thread(_probe)
    if not info:
        raise ValueError("No metadata found - unsupported URL or site blocking")
    # Cache result for fast second probe (callback after keyboard) + Redis shared
    try:
        from app.core.downloader import _set_probe_cache

        _set_probe_cache(url, info, quality)
        # Also cache in Redis shared (worker vs bot)
        from app.services.redis import cache_probe

        # Use probe_cache_ttl from settings
        ttl = getattr(s, "probe_cache_ttl", 900)
        # Schedule without awaiting too long? But we can await
        try:
            await cache_probe(url, info, ttl=ttl)
        except Exception:
            pass
    except Exception:
        pass
    return info
