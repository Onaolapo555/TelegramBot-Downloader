from __future__ import annotations

import asyncio
from typing import Any

import yt_dlp

from app.config import get_settings


async def probe_metadata(url: str) -> dict[str, Any]:
    """Fast metadata probe without downloading. Validates URL via yt-dlp extract_info."""
    s = get_settings()
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not s.allow_playlist,
        "extract_flat": False,
        "skip_download": True,
        "socket_timeout": 15,
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
    return info
