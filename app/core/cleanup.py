from __future__ import annotations

import asyncio
import logging
import time

from app.config import get_settings

log = logging.getLogger(__name__)


async def cleanup_loop() -> None:
    """Background task: delete files older than TMP_CLEANUP_SECONDS in DOWNLOAD_DIR."""
    s = get_settings()
    interval = 60  # check every minute
    while True:
        try:
            now = time.time()
            cutoff = s.tmp_cleanup_seconds
            for p in s.download_dir.glob("*"):
                if p.name == ".gitkeep":
                    continue
                try:
                    age = now - p.stat().st_mtime
                    if age > cutoff:
                        if p.is_file():
                            p.unlink(missing_ok=True)
                            log.info("cleanup deleted %s age=%.0fs", p.name, age)
                        elif p.is_dir():
                            # just in case
                            import shutil
                            shutil.rmtree(p, ignore_errors=True)
                except Exception as e:
                    log.warning("cleanup error %s: %s", p, e)
        except Exception as e:
            log.warning("cleanup loop error: %s", e)
        await asyncio.sleep(interval)


def cleanup_sync() -> int:
    """Synchronous one-shot cleanup, returns count deleted."""
    s = get_settings()
    now = time.time()
    deleted = 0
    for p in s.download_dir.glob("*"):
        if p.name == ".gitkeep":
            continue
        try:
            if now - p.stat().st_mtime > s.tmp_cleanup_seconds and p.is_file():
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            pass
    return deleted
