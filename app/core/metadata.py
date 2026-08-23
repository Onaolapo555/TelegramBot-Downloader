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
    # Optional cookies: if cookies.txt exists in COOKIES_DIR, yt-dlp can use it
    # For now, let yt-dlp auto-discover; advanced: pass cookiefile per domain

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
