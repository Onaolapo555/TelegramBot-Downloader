from __future__ import annotations

import html


def human_bytes(num: float | None) -> str:
    if num is None:
        return "unknown"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if s else f"{h}h {m}m"


def format_caption(meta: dict, quality_label: str | None = None, filesize: int | None = None) -> str:
    title = html.escape((meta.get("title") or "Untitled")[:300])
    uploader = html.escape((meta.get("uploader") or meta.get("channel") or "Unknown")[:120])
    duration = human_duration(meta.get("duration"))
    views = meta.get("view_count")
    views_str = f"{views:,}" if isinstance(views, int) else "—"
    ext = meta.get("ext") or "mp4"
    q = f" • {quality_label}" if quality_label else ""
    source = html.escape(meta.get("extractor_key") or meta.get("extractor") or "unknown")
    url = meta.get("webpage_url") or meta.get("original_url") or ""
    # Filesize if known
    size_str = human_bytes(filesize) if filesize else ""
    # Resolution if available
    wh = ""
    if meta.get("width") and meta.get("height"):
        wh = f" • {meta['width']}x{meta['height']}"

    lines = [
        f"🎬 <b>{title}</b>",
        f"👤 {uploader}  •  ⏱ {duration}  •  👁 {views_str}{q}{wh}",
        f"📦 {ext}" + (f" • {size_str}" if size_str else "") + f"  •  🌐 {source}",
    ]
    if url:
        lines.append(f'🔗 <a href="{html.escape(url)}">Source</a>')
    lines.append("⚡️ via UniMedia Bot")
    return "\n".join(lines)


def format_probe_summary(meta: dict) -> str:
    """One-liner for logs / debugging probe."""
    title = (meta.get("title") or "Untitled")[:80]
    dur = human_duration(meta.get("duration"))
    ext = meta.get("extractor_key") or meta.get("extractor") or "?"
    size = human_bytes(meta.get("filesize") or meta.get("filesize_approx"))
    return f"{title} | {dur} | {size} | {ext}"
