from __future__ import annotations

import asyncio
import logging
import time

from app.config import get_settings

log = logging.getLogger(__name__)


async def cleanup_loop() -> None:
    """Background task: delete files older than TMP_CLEANUP_SECONDS and prune old DB jobs (lightweight DB)."""
    s = get_settings()
    interval = 60  # check every minute for files
    last_job_prune = 0.0
    while True:
        try:
            now = time.time()
            # File cleanup: use file_retention_seconds if set else tmp_cleanup_seconds
            cutoff = s.file_retention_seconds if s.file_retention_seconds is not None else s.tmp_cleanup_seconds
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
                            import shutil

                            shutil.rmtree(p, ignore_errors=True)
                except Exception as e:
                    log.warning("cleanup error %s: %s", p, e)

            # DB lightweight prune: hourly delete old done/failed jobs older than retention
            if now - last_job_prune > s.job_cleanup_interval_seconds:
                last_job_prune = now
                try:
                    from app.services.job_service import prune_old_jobs

                    # Run prune in thread to not block loop if DB slow
                    deleted = await prune_old_jobs(days=s.job_retention_days)
                    if deleted:
                        log.info("job_prune deleted=%s retention_days=%s", deleted, s.job_retention_days)
                except Exception as e:
                    log.warning("job_prune error: %s", e)
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
