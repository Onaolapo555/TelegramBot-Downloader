from __future__ import annotations

import re
from urllib.parse import urlparse

# Robust URL regex - covers most social platforms + youtu.be short links
_URL_RE = re.compile(
    r"(https?://[^\s<>\"']+|www\.[^\s<>\"']+|t\.me/[^\s<>\"']+|youtu\.be/[^\s<>\"']+)",
    re.IGNORECASE,
)

# Known domains for fast-path detection (used for UX, not strict validation - yt-dlp validates)
_KNOWN_DOMAINS = {
    "youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com",
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "instagram.com", "instagr.am",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch", "fb.com",
    "reddit.com", "redd.it",
    "vimeo.com", "twitch.tv", "clips.twitch.tv",
    "linkedin.com", "pinterest.com", "pin.it",
    "soundcloud.com", "dailymotion.com", "bilibili.com",
}


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    raw = _URL_RE.findall(text)
    # normalize www. without scheme
    out: list[str] = []
    for u in raw:
        if u.startswith("www."):
            u = "https://" + u
        # strip trailing punctuation
        u = u.rstrip(".,!?)]}>\"'")
        out.append(u)
    # dedup preserve order
    seen = set()
    deduped = []
    for u in out:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def is_url(text: str) -> bool:
    return bool(extract_urls(text))


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("www."):
        url = "https://" + url
    return url


def domain_of(url: str) -> str | None:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return None


def is_known_platform(url: str) -> bool:
    d = domain_of(url)
    if not d:
        return False
    return any(d == k or d.endswith("." + k) for k in _KNOWN_DOMAINS)
