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
    # For YouTube, keep default extractor (no forced client) — gives 45 formats vs 4 with forced android, SABR-safe.
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
        from app.core.downloader import (
            _is_youtube_url,
            _sanitize_cookie_content,
            is_cookie_encoding_error,
            is_format_not_available_error,
            is_youtube_bot_error,
            is_youtube_reload_error,
        )

        attempts = 5 if _is_youtube_url(url) else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and "entries" in info:
                        entries = list(info["entries"])
                        if entries:
                            first = next((e for e in entries if e), None)
                            if first:
                                first["playlist_title"] = info.get("title")
                                return first
                    return info
            except Exception as e:
                last_exc = e
                if _is_youtube_url(url) and is_youtube_bot_error(e):
                    if attempt + 1 < attempts:
                        opts["extractor_args"] = {
                            "youtube": {"player_client": ["tv_embedded", "android", "web"], "player_skip": ["webpage"]}
                        }
                        continue
                elif _is_youtube_url(url) and (is_youtube_reload_error(e) or is_cookie_encoding_error(e)):
                    if attempt + 1 < attempts:
                        try:
                            from app.config import get_settings as _gs

                            cf = opts.get("cookiefile")
                            if cf:
                                p = _gs().cookies_dir / "youtube.txt"
                                if p.exists():
                                    txt = p.read_text(encoding="utf-8", errors="ignore")
                                    sanitized = _sanitize_cookie_content(txt)
                                    if sanitized != txt:
                                        p.write_text(sanitized, encoding="utf-8")
                        except Exception:
                            pass
                        opts.pop("cookiefile", None)
                        opts.pop("extractor_args", None)
                        continue
                elif _is_youtube_url(url) and is_format_not_available_error(e):
                    if attempt + 1 < attempts:
                        opts.pop("extractor_args", None)
                        continue
                else:
                    raise
        if last_exc and is_youtube_bot_error(last_exc):
            raise RuntimeError(
                "YouTube bot check failed (Sign in to confirm you’re not a bot). "
                "Add cookies to data/cookies/youtube.txt or set YOUTUBE_COOKIES env var. "
                "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
            ) from last_exc
        if last_exc and (is_youtube_reload_error(last_exc) or is_cookie_encoding_error(last_exc)):
            raise RuntimeError(
                "YouTube cookies invalid (latin-1 at pos 631). Sanitized and retried without cookies — still failed. Re-export as base64 -w0."
            ) from last_exc
        if last_exc and is_format_not_available_error(last_exc):
            raise RuntimeError(
                "YouTube: Requested format is not available for this video. Try Best/720p/Audio."
            ) from last_exc
        if last_exc:
            raise last_exc
        raise ValueError("No metadata found")

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
