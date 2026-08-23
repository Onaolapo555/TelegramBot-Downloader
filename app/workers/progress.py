from __future__ import annotations

import time
from dataclasses import dataclass

from app.utils.format import human_bytes


@dataclass
class ProgressState:
    last_edit: float = 0.0
    last_text: str = ""


def format_progress_text(d: dict, url: str) -> str | None:
    """Return text to edit message with, or None if throttled/not downloading."""
    if d.get("status") != "downloading":
        return None
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    downloaded = d.get("downloaded_bytes") or 0
    pct = ""
    if total:
        pct = f"{downloaded / total * 100:.1f}%"
    else:
        pct = d.get("_percent_str") or "?%"
    speed = d.get("_speed_str") or (human_bytes(d.get("speed")) + "/s" if d.get("speed") else "—")
    eta = d.get("_eta_str") or (f"{int(d['eta'])}s" if d.get("eta") else "—")
    downloaded_s = human_bytes(downloaded)
    total_s = human_bytes(total)
    return f"⬇️ <b>Downloading… {pct}</b>\n{downloaded_s} / {total_s} • {speed} • ETA {eta}\n<code>{url[:80]}</code>"


def should_throttle(state: ProgressState, interval: float = 1.8) -> bool:
    now = time.time()
    if now - state.last_edit < interval:
        return True
    state.last_edit = now
    return False
