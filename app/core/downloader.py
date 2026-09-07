from __future__ import annotations

import logging
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yt_dlp

from app.config import get_settings

log = logging.getLogger(__name__)

# In-memory probe cache for insane preparing speed (url+quality -> meta)
_PROBE_CACHE: dict[str, tuple[dict, float]] = {}

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


def _is_youtube_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse

        netloc = urlparse(url).netloc.lower()
        return "youtube.com" in netloc or "youtu.be" in netloc or "youtube-nocookie.com" in netloc
    except Exception:
        return False


def _resolve_cookiefile(url: str | None = None) -> str | None:
    """Phase-5: per-domain cookiefile resolution. Checks domain-specific FIRST, then generic.

    Fixed: previously generic cookies.txt masked youtube.txt (caused YouTube bot error
    even when youtube.txt existed). Now youtube.txt is preferred for YouTube URLs.
    """
    s = get_settings()
    # Ensure YOUTUBE_COOKIES env materialized to file if provided (for Render/Vercel ephemeral FS)
    try:
        # Call helper to materialize env cookies if needed (non-blocking)
        _maybe_write_youtube_cookies_from_env()
    except Exception:
        pass

    if url:
        try:
            from urllib.parse import urlparse

            netloc = urlparse(url).netloc.lower().lstrip("www.")
            # Map known domains to cookie files
            domain_map = {
                "youtube.com": "youtube.txt",
                "youtu.be": "youtube.txt",
                "youtube-nocookie.com": "youtube.txt",
                "instagram.com": "instagram.txt",
                "tiktok.com": "tiktok.txt",
                "twitter.com": "twitter.txt",
                "x.com": "twitter.txt",
                "facebook.com": "facebook.txt",
                "fb.watch": "facebook.txt",
            }
            for dom, fname in domain_map.items():
                if netloc == dom or netloc.endswith("." + dom):
                    cand = s.cookies_dir / fname
                    if cand.exists():
                        return str(cand)
            # Fallback: try <netloc>.txt
            cand = s.cookies_dir / f"{netloc.split('.')[0]}.txt"
            if cand.exists():
                return str(cand)
        except Exception:
            pass
    # 2. Generic fallback
    generic = s.cookies_dir / "cookies.txt"
    if generic.exists():
        return str(generic)
    return None


def _sanitize_cookie_content(content: str) -> str:
    """Make Netscape cookie file latin-1 safe — yt-dlp/http.cookiejar uses latin-1 for Cookie header.
    Drops or replaces non-latin-1 cookie values that would cause 'latin-1 codec can't encode'."""
    lines: list[str] = []
    for line in content.splitlines():
        if not line or line.startswith("#"):
            lines.append(line)
            continue
        # Netscape format: domain \t flag \t path \t secure \t expiration \t name \t value
        parts = line.split("\t")
        if len(parts) >= 7:
            val = parts[6]
            try:
                val.encode("latin-1")
            except UnicodeEncodeError:
                # Sanitize value: replace non-latin-1 with '?' or skip line if value is essential
                # Keep line but replace problematic chars
                parts[6] = val.encode("latin-1", errors="replace").decode("latin-1")
                log.warning("sanitized non-latin-1 cookie %s", parts[5])
            lines.append("\t".join(parts))
        else:
            # Try to keep line if it can be encoded, otherwise skip
            try:
                line.encode("latin-1")
                lines.append(line)
            except UnicodeEncodeError:
                log.warning("skipped non-latin-1 cookie line")
                continue
    return "\n".join(lines) + "\n"


def _maybe_write_youtube_cookies_from_env() -> None:
    """Render ephemeral fix: if YOUTUBE_COOKIES env var is set, write to data/cookies/youtube.txt.

    Supports both plain Netscape content and base64-encoded content.
    Handles base64 with whitespace/newlines and \\n literals.
    Sanitizes to latin-1 to avoid 'latin-1 codec can't encode \\u30d6' on yt-dlp.
    """
    import base64
    import os

    s = get_settings()
    raw = os.getenv("YOUTUBE_COOKIES") or getattr(s, "youtube_cookies", None)  # type: ignore
    if not raw:
        return
    raw = raw.strip()
    if not raw:
        return
    content = raw
    # Handle \\n literal first (common when env var pasted with escaped newlines)
    if "\\n" in raw and "# Netscape" not in raw:
        # If raw contains literal \n but not real newlines, it's likely escaped
        # Only treat as literal if raw has no real newline
        if "\n" not in raw:
            content = raw.replace("\\n", "\n")
            raw = content
    # Detect base64: if not starting with "#"
    if not content.lstrip().startswith("#"):
        try:
            # Remove whitespace/newlines for base64
            b64_str = "".join(content.split())
            # Basic b64 charset check
            if len(b64_str) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in b64_str):
                padded = b64_str + "=" * (-len(b64_str) % 4)
                decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="ignore")
                if "# Netscape" in decoded or "youtube.com" in decoded or "LOGIN_INFO" in decoded:
                    content = decoded
        except Exception:
            pass
    # Normalize any remaining \n literals
    if "\\n" in content:
        content = content.replace("\\n", "\n")
    # Sanitize to latin-1
    try:
        content.encode("latin-1")
    except UnicodeEncodeError:
        content = _sanitize_cookie_content(content)
    try:
        s.ensure_dirs()
        target = s.cookies_dir / "youtube.txt"
        if target.exists():
            try:
                if target.read_text(encoding="utf-8", errors="ignore") == content:
                    return
            except Exception:
                pass
        target.write_text(content, encoding="utf-8")
        log.info("youtube_cookies_from_env_written path=%s", str(target))
    except Exception as e:
        log.warning("youtube_cookies_write_failed: %s", e)


def is_youtube_bot_error(exc: Exception | str) -> bool:
    """Detect YouTube 'Sign in to confirm you're not a bot' error. Works for both straight and curly apostrophes."""
    msg = str(exc).lower()
    if "sign in to confirm you" in msg and "bot" in msg:
        return True
    if "confirm you" in msg and "not a bot" in msg:
        return True
    if "use --cookies" in msg and "youtube" in msg:
        return True
    if "sign in to confirm" in msg and "not a bot" in msg:
        return True
    return False


def is_format_not_available_error(exc: Exception | str) -> bool:
    """Detect yt-dlp 'Requested format is not available' — common for YouTube shorts with strict format."""
    msg = str(exc).lower()
    return "requested format is not available" in msg and "use --list-formats" in msg


def is_youtube_reload_error(exc: Exception | str) -> bool:
    """Detect YouTube 'The page needs to be reloaded.' — often caused by stale/incomplete youtube cookies."""
    msg = str(exc).lower()
    return "the page needs to be reloaded" in msg and "youtube" in msg


def is_cookie_encoding_error(exc: Exception | str) -> bool:
    """Detect 'latin-1 codec can't encode' from http.cookiejar when cookie value contains unicode like \\u30d6."""
    msg = str(exc).lower()
    return ("latin-1" in msg and "can't encode" in msg) or ("codec can't encode" in msg and "ordinal not in range" in msg) or ("'latin-1'" in msg and "ordinal not in range" in msg)


def youtube_bot_help_text(lang: str = "en") -> str:
    """User-friendly help for YouTube bot error — does not break other sites."""
    return (
        "❌ <b>YouTube is blocking downloads</b> (Sign in to confirm you’re not a bot)\n\n"
        "YouTube now requires cookies for many videos. This is <b>not a bot bug</b> — it affects all downloaders.\n\n"
        "<b>Quick fix for bot owner (one-time, 2 min):</b>\n"
        "1. Install desktop Chrome/Firefox → open YouTube logged-in.\n"
        "2. Install extension <b>Get cookies.txt LOCALLY</b> → Export → copy.\n"
        "3. On Render: set env var <code>YOUTUBE_COOKIES</code> to the file content (or base64) — auto-saved to <code>data/cookies/youtube.txt</code>.\n"
        "   Or locally/Docker: save as <code>data/cookies/youtube.txt</code> (Netscape format).\n"
        "4. Redeploy/restart bot — YouTube will work again.\n\n"
        "<b>Workaround without cookies:</b> Try again in a minute — bot auto-retries with Android/iOS clients which often bypass the check.\n"
        "If still fails, the video may be age-restricted/private and <i>does</i> need cookies.\n\n"
        "💡 Other sites (TikTok, IG, X, FB, etc.) are unaffected."
    )


def youtube_format_help_text() -> str:
    """Friendly text for 'Requested format is not available' on YouTube."""
    return (
        "❌ <b>YouTube: Requested format not available</b>\n\n"
        "The video exists but the selected quality (e.g., 1080p mp4) isn't offered for this Short/video "
        "(YouTube SABR experiment limits android formats to 360p). The bot will now try with a more permissive format.\n\n"
        "💡 <b>Try:</b> Tap <b>Best</b> or <b>720p</b> or <b>Audio only</b> — those fallback to <code>best</code> which works for Shorts.\n"
        "If you still see this, the bot auto-retries with <code>best</code> on next attempt. "
        "Updating <code>yt-dlp</code> (auto-updates every 6h) also helps. Other sites unaffected."
    )


def youtube_reload_help_text() -> str:
    """Friendly text for 'The page needs to be reloaded' — stale cookies."""
    return (
        "❌ <b>YouTube: Page needs to be reloaded</b> (cookies expired/incomplete)\n\n"
        "Your <code>youtube.txt</code> cookies are stale or incomplete (missing CONSENT/visitor data). "
        "This Short/video actually works <b>without</b> cookies — bot will retry without them.\n\n"
        "<b>Fix:</b> Delete <code>data/cookies/youtube.txt</code> (or clear <code>YOUTUBE_COOKIES</code> env) and re-export fresh cookies via "
        "<b>Get cookies.txt LOCALLY</b> while logged into YouTube, or leave cookies empty for public videos. "
        "Public Shorts don't need cookies. Private/age-restricted do.\n\n"
        "💡 Bot auto-retries without cookies on this error. Try sending link again or tap Best."
    )


def youtube_cookie_help_text() -> str:
    """Friendly text for latin-1 cookie encoding error (unicode in cookie value)."""
    return (
        "❌ <b>YouTube cookies contain invalid characters</b> (<code>latin-1</code> encode error)\n\n"
        "Your <code>YOUTUBE_COOKIES</code> / <code>youtube.txt</code> has a cookie value with unicode (e.g., <code>\\u30d6</code> at pos 631) "
        "that `http.cookiejar` can't send (`latin-1` only). This happens when exporting while YouTube sets non-ascii cookies or base64 was pasted with wrong encoding.\n\n"
        "<b>Fix:</b> Re-export <code>youtube.txt</code> via <b>Get cookies.txt LOCALLY</b> (ensure file is UTF-8, no extra wrapping), "
        "then on Render set <code>YOUTUBE_COOKIES</code> as <b>base64 single-line</b>: <code>base64 -w0 data/cookies/youtube.txt</code> and paste that string. "
        "Or run <code>base64 -w0</code> on Linux / <code>certutil -encode</code> on Windows. Bot now sanitizes cookies and auto-retries without them for this video — try again.\n\n"
        "💡 Public videos work without cookies; only keep cookies for private/age-restricted."
    )


def _get_probe_cache(url: str, quality: str | None = None) -> dict | None:
    key = f"{url}::{quality or ''}"
    entry = _PROBE_CACHE.get(key)
    if entry:
        meta, exp = entry
        if time.monotonic() < exp:
            return meta
        _PROBE_CACHE.pop(key, None)
    return None


def _set_probe_cache(url: str, meta: dict, quality: str | None = None, ttl: int | None = None):
    if ttl is None:
        try:
            ttl = get_settings().probe_cache_ttl
        except Exception:
            ttl = 900
    key = f"{url}::{quality or ''}"
    _PROBE_CACHE[key] = (meta, time.monotonic() + ttl)


def build_ydl_opts(
    quality: Quality,
    outtmpl: str,
    progress_hook: Callable[[dict], None] | None = None,
    *,
    no_thumb: bool = False,
    url: str | None = None,
) -> dict:
    s = get_settings()
    fmt = FORMAT_MAP.get(quality, FORMAT_MAP["best"])
    # Fast mode override: skip thumbnail for max speed if configured
    if s.fast_mode:
        no_thumb = True

    opts: dict = {
        "format": fmt,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4" if not quality.startswith("audio") else None,
        "noplaylist": not s.allow_playlist,
        "quiet": True,
        "no_warnings": True,
        # Insane speed tuning: yt-dlp native chunking + aria2c for http
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
        "skip_unavailable_fragments": True,
        "keepvideo": False,
        # Exact yt-dlp fast approach: prefer h264/aac mp4, avoid re-encode
        "format_sort": ["res", "fps", "codec:h264", "size", "br"],
        "postprocessor_args": {
            "ffmpeg": ["-hwaccel", "auto"]  # use hwaccel if available for merge speed
        }
        if not s.fast_mode
        else {},
    }
    # Remove empty dict postprocessor_args if fast
    if not opts.get("postprocessor_args"):
        opts.pop("postprocessor_args", None)

    # aria2c external downloader - insanely fast multi-connection for http (non-HLS)
    # yt-dlp will use aria2c if available and for http/https protocols
    if s.use_aria2 and shutil.which("aria2c"):
        max_conn = max(1, min(32, s.aria2c_max_connections))
        opts["external_downloader"] = "aria2c"
        opts["external_downloader_args"] = {
            "http": ["-x", str(max_conn), "-s", str(max_conn), "-k", "1M", "--file-allocation=none", "--async-dns=false"],
            "https": ["-x", str(max_conn), "-s", str(max_conn), "-k", "1M", "--file-allocation=none", "--async-dns=false"],
        }
        # For HLS/DASH, yt-dlp still uses concurrent_fragment_downloads; aria2c handles direct http
        # Ensure we still keep fragment concurrency

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

        # Optional subtitle handling (Phase-5) — respects ENABLE_SUBTITLES
        if s.enable_subtitles:
            langs = [x.strip() for x in s.subtitle_langs.split(",") if x.strip()]
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = langs or ["en"]
            opts["subtitlesformat"] = "srt"

    # Proxy (Phase-5) — e.g., http://proxy:8080 for geo-blocked content
    if s.ytdlp_proxy:
        opts["proxy"] = s.ytdlp_proxy

    # Cookies per-domain (Phase-5)
    cookiefile = _resolve_cookiefile(url=url)
    if cookiefile:
        opts["cookiefile"] = cookiefile

    # YouTube: SABR-safe handling — don't force android (limits to 360p & triggers SABR skip).
    # Default yt-dlp (no extractor_args) gives 45 formats vs 4 with forced android; keep default
    # and only use fallback clients on bot/format errors in download_media/probe.
    # Use ext-agnostic format for YouTube to avoid "Requested format is not available" on shorts
    # where SABR skips android https formats.
    if _is_youtube_url(url) and not quality.startswith("audio"):
        height_map = {"best": "1080", "1080": "1080", "720": "720", "480": "480", "360": "360"}
        if quality in height_map:
            h = height_map[quality]
            # Generic fallback without ext filter — SABR-safe, still prefers best
            opts["format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        opts["age_limit"] = None

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
    Raises on failure. Includes YouTube bot-bypass retry — does not affect other sites.
    """
    s = get_settings()
    s.ensure_dirs()

    # Unique outtmpl per job to avoid collisions: <download_dir>/<uuid>.%(ext)s
    job_id = uuid.uuid4().hex[:10]
    outtmpl = str(s.download_dir / f"{job_id}_%(id)s.%(ext)s")

    opts = build_ydl_opts(quality, outtmpl, progress_hook, url=url)

    log.info("yt-dlp start url=%s quality=%s job=%s cookie=%s", url, quality, job_id, bool(opts.get("cookiefile")))

    last_exc: Exception | None = None
    # YouTube retry: up to 5 attempts — default, bot fallback, cookie reload/encoding fallback, format fallback
    attempts = 5 if _is_youtube_url(url) else 1
    for attempt in range(attempts):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            # Bot check — retry with fallback client
            if _is_youtube_url(url) and is_youtube_bot_error(e):
                if attempt + 1 < attempts:
                    log.warning("youtube_bot_detected retrying with fallback client: %s", str(e)[:300])
                    opts["extractor_args"] = {
                        "youtube": {
                            "player_client": ["tv_embedded", "android", "web"],
                            "player_skip": ["webpage"],
                        }
                    }
                    try:
                        for p in s.download_dir.glob(f"{job_id}_*"):
                            if p.is_file():
                                p.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    continue
                # Last attempt still bot error — will raise friendly below
            # Cookie encoding / reload error (stale unicode cookies) — retry WITHOUT cookies
            elif _is_youtube_url(url) and (is_youtube_reload_error(e) or is_cookie_encoding_error(e)):
                if attempt + 1 < attempts:
                    log.warning("youtube_cookie_error retrying without cookies: %s", str(e)[:300])
                    # Sanitize file for future runs
                    try:
                        cf = opts.get("cookiefile")
                        if cf:
                            p = s.cookies_dir / "youtube.txt"
                            if p.exists():
                                txt = p.read_text(encoding="utf-8", errors="ignore")
                                sanitized = _sanitize_cookie_content(txt)
                                if sanitized != txt:
                                    p.write_text(sanitized, encoding="utf-8")
                                    log.info("sanitized youtube cookies for latin-1")
                    except Exception:
                        pass
                    opts.pop("cookiefile", None)
                    opts.pop("extractor_args", None)
                    opts["format"] = "best"
                    try:
                        for p in s.download_dir.glob(f"{job_id}_*"):
                            if p.is_file():
                                p.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    continue
            # Format not available — retry with permissive best
            elif _is_youtube_url(url) and is_format_not_available_error(e):
                if attempt + 1 < attempts:
                    log.warning("youtube_format_not_available retrying with best: %s", str(e)[:300])
                    opts["format"] = "best"
                    opts.pop("extractor_args", None)
                    try:
                        for p in s.download_dir.glob(f"{job_id}_*"):
                            if p.is_file():
                                p.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    continue
                # else will raise friendly below
            else:
                raise
    if last_exc and is_youtube_bot_error(last_exc):
        raise RuntimeError(
            "YouTube bot check failed (Sign in to confirm you’re not a bot). "
            "Fix: add YouTube cookies to data/cookies/youtube.txt or set YOUTUBE_COOKIES env var. "
            "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
        ) from last_exc
    if last_exc and (is_youtube_reload_error(last_exc) or is_cookie_encoding_error(last_exc)):
        raise RuntimeError(
            "YouTube cookies invalid (latin-1 / reload error at pos 631). Sanitized and retried without cookies but still failed. "
            "Re-export fresh youtube.txt via Get cookies.txt LOCALLY as base64 single-line: base64 -w0 data/cookies/youtube.txt"
        ) from last_exc
    if last_exc and is_format_not_available_error(last_exc):
        raise RuntimeError(
            "YouTube: Requested format is not available for this video (often Shorts with SABR). "
            "Try Best/720p/Audio or ensure cookies are valid. Auto-retried with best but still failed. "
            "See yt-dlp --list-formats."
        ) from last_exc
    if last_exc:
        raise last_exc

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
