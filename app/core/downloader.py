from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yt_dlp

from app.config import get_settings

log = logging.getLogger(__name__)

Quality = Literal["best", "1080", "720", "480", "360", "audio_mp3", "audio_m4a"]

# Format selectors per AGENTS.md:3
FORMAT_MAP: dict[Quality, str] = {
    # Best up to 1080p mp4 + m4a, fallback
    "best": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "1080": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best",
    "720": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best",
    "480": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/best",
    "360": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/best",
    # audio: bestaudio, will be postprocessed to mp3/m4a
    "audio_mp3": "bestaudio/best",
    "audio_m4a": "bestaudio[ext=m4a]/bestaudio/best",
}


def estimate_filesize(meta: dict, quality: Quality) -> int | None:
    """Rough-estimate filesize from metadata for auto-downgrade logic. Returns bytes or None."""
    # Try approximate filesize from formats
    try:
        formats = meta.get("formats") or []
        # find best matching height for quality
        target_h = {"best": 1080, "1080": 1080, "720": 720, "480": 480, "360": 360}.get(quality)
        if target_h and formats:
            # pick formats with height <= target and filesize
            cand = [f for f in formats if f.get("height") and f["height"] <= target_h and f.get("filesize")]
            if cand:
                # take max height candidate
                cand.sort(key=lambda f: f["height"])
                return int(cand[-1]["filesize"])
        # fallback: duration * tbr * 125 (kbit to bytes)
        dur = meta.get("duration")
        tbr = meta.get("tbr") or meta.get("abr")  # kbps
        if dur and tbr:
            return int(float(tbr) * 1000 / 8 * float(dur))
        # yt-dlp sometimes gives filesize_approx
        if meta.get("filesize_approx"):
            return int(meta["filesize_approx"])
        if meta.get("filesize"):
            return int(meta["filesize"])
    except Exception:
        return None
    return None


def auto_downgrade_quality(meta: dict, requested: Quality, limit_bytes: int | None = None) -> Quality:
    """If estimate > limit (1.9GB), downgrade to smaller quality to stay under Telegram Local limit."""
    from app.config import get_settings

    if limit_bytes is None:
        # Use configurable threshold, fallback to 1.9GB
        try:
            limit_bytes = get_settings().large_file_threshold_bytes
        except Exception:
            limit_bytes = 1900 * 1024 * 1024
    est = estimate_filesize(meta, requested)
    if est is None or est <= limit_bytes:  # type: ignore
        return requested
    # Ordered from largest to smallest
    order: list[Quality] = ["best", "1080", "720", "480", "360", "audio_m4a", "audio_mp3"]
    # Find current index
    try:
        idx = order.index(requested)
    except ValueError:
        return requested
    # Walk to smaller qualities until estimate fits or we reach smallest
    for q in order[idx + 1 :]:
        est2 = estimate_filesize(meta, q)
        if est2 is None:
            return q  # if unknown, try smaller
        if est2 <= limit_bytes:
            return q
    return order[-1]  # ultimate fallback audio


def build_ydl_opts(
    quality: Quality,
    outtmpl: str,
    progress_hook: Callable[[dict], None] | None = None,
    *,
    no_thumb: bool = False,
) -> dict:
    s = get_settings()
    fmt = FORMAT_MAP.get(quality, FORMAT_MAP["best"])

    opts: dict = {
        "format": fmt,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4" if not quality.startswith("audio") else None,
        "noplaylist": not s.allow_playlist,
        "quiet": True,
        "no_warnings": True,
        # Speed / reliability (AGENTS.md 2.4): max concurrent fragments + retries
        "concurrent_fragment_downloads": 16,
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "progress_hooks": [progress_hook] if progress_hook else [],
        "noprogress": False,
        "prefer_free_formats": False,
        # Improve fragment retry & skip unavailable
        "skip_unavailable_fragments": True,
        "keepvideo": False,
    }

    # remove None values (yt-dlp doesn't like None for merge_output_format)
    opts = {k: v for k, v in opts.items() if v is not None}

    # Audio postprocessors
    if quality == "audio_mp3":
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            },
            {"key": "FFmpegMetadata"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"} if not no_thumb else None,
        ]
        opts["postprocessors"] = [p for p in opts["postprocessors"] if p]
        opts["merge_output_format"] = None
        opts["writethumbnail"] = not no_thumb
    elif quality == "audio_m4a":
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
        ]
        opts["merge_output_format"] = None
    else:
        # Video: metadata + thumbnail handling
        opts["postprocessors"] = [
            {"key": "FFmpegMetadata"},
        ]
        if not no_thumb:
            opts["writethumbnail"] = True
            # Embed thumbnail if possible (requires image, but fail gracefully)
            # We add EmbedThumbnail postprocessor; if ffmpeg has no image, it will warn but not fail
            opts["postprocessors"].append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

        # Optional subtitle handling: download auto subs if available (not burned)
        # Controlled via env later; for Phase-3 we enable metadata but not hard burn
        # Users can request via quality param "subs" in future; keep off by default to save speed

    # Cookies: if data/cookies/<domain>.txt exists, use it. Also try generic cookies.txt
    # yt-dlp supports --cookies; we just check generic file
    generic_cookie = s.cookies_dir / "cookies.txt"
    if generic_cookie.exists():
        opts["cookiefile"] = str(generic_cookie)

    # Limit playlist items
    if s.allow_playlist:
        opts["playlistend"] = s.playlist_max_items

    # Trim outtmpl extension handling - yt-dlp adds ext
    return opts


def download_media(
    url: str,
    quality: Quality = "best",
    progress_hook: Callable[[dict], None] | None = None,
) -> Path:
    """
    Synchronous download (run in thread). Returns Path to downloaded file.
    Raises on failure.
    """
    s = get_settings()
    s.ensure_dirs()

    # Unique outtmpl per job to avoid collisions: <download_dir>/<uuid>.%(ext)s
    job_id = uuid.uuid4().hex[:10]
    outtmpl = str(s.download_dir / f"{job_id}_%(id)s.%(ext)s")

    opts = build_ydl_opts(quality, outtmpl, progress_hook)

    log.info("yt-dlp start url=%s quality=%s job=%s", url, quality, job_id)

    with yt_dlp.YoutubeDL(opts) as ydl:
        # Clean URL (strip tracking params that break some extractors? but yt-dlp handles)
        ydl.download([url])

    # Find the downloaded file (yt-dlp may have created multiple, pick newest largest)
    # Since outtmpl includes %(id)s, we need to glob job_id*
    candidates = list(s.download_dir.glob(f"{job_id}_*"))
    if not candidates:
        raise FileNotFoundError(f"yt-dlp reported success but no file found for job {job_id}")

    # Pick largest file (video > thumbnail)
    candidates = [p for p in candidates if p.is_file()]
    # Filter out .json/.jpg etc if any? but we didn't enable writethumbnail
    # Prefer .mp4/.mp3/.m4a/.mkv/.webm over .jpg
    media_exts = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".ogg", ".mov", ".avi"}
    media = [p for p in candidates if p.suffix.lower() in media_exts]
    pool = media if media else candidates
    pool.sort(key=lambda p: p.stat().st_size, reverse=True)
    chosen = pool[0]

    log.info("yt-dlp done job=%s file=%s size=%s", job_id, chosen.name, chosen.stat().st_size)
    return chosen
