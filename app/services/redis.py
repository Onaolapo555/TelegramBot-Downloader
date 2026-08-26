from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# Global redis client singleton (aioredis via redis.asyncio)
_redis_client: Any | None = None
_redis_available: bool | None = None
_lock = asyncio.Lock()


async def get_redis() -> Any | None:
    """
    Return async Redis client or None if Redis not reachable.
    Lazy-connects and caches. Graceful fallback: returns None on failure.
    """
    global _redis_client, _redis_available
    if _redis_client is not None:
        # test liveness lazily every call? just return
        return _redis_client
    if _redis_available is False:
        return None

    s = get_settings()
    try:
        import redis.asyncio as redis  # type: ignore

        client = redis.from_url(s.redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
        # ping test
        await client.ping()
        _redis_client = client
        _redis_available = True
        log.info("redis_connected url=%s", s.redis_url.split("@")[-1])
        return client
    except Exception as e:
        _redis_available = False
        log.warning("redis_unavailable - falling back to in-memory: %s", e)
        return None


async def close_redis() -> None:
    global _redis_client, _redis_available
    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception:
            pass
        _redis_client = None
        _redis_available = None


# ---------------------------------------------------------------------------
# URL Cache (hash -> url) with TTL, Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

_URL_CACHE_TTL = 3600  # 1 hour
_mem_url_cache: dict[str, tuple[str, float]] = {}  # hash -> (url, expiry_monotonic)


async def cache_url_redis(url: str, h: str) -> None:
    """Store url hash with TTL."""
    client = await get_redis()
    if client is not None:
        try:
            await client.setex(f"urlcache:{h}", _URL_CACHE_TTL, url)
            return
        except Exception as e:
            log.warning("redis setex failed, using memory: %s", e)
    # fallback memory
    _mem_url_cache[h] = (url, time.monotonic() + _URL_CACHE_TTL)


async def get_cached_url_redis(h: str) -> str | None:
    client = await get_redis()
    if client is not None:
        try:
            val = await client.get(f"urlcache:{h}")
            if val:
                return val
        except Exception:
            pass
    # fallback memory with expiry check
    entry = _mem_url_cache.get(h)
    if entry:
        url, exp = entry
        if time.monotonic() < exp:
            return url
        else:
            _mem_url_cache.pop(h, None)
    return None


# ---------------------------------------------------------------------------
# Rate limiting helpers (Redis INCR + EXPIRE, fallback to in-memory)
# ---------------------------------------------------------------------------

_mem_hits: dict[int, list[float]] = {}  # user_id -> list of timestamps


async def is_rate_limited(user_id: int, max_per_hour: int, min_interval: float = 2.0) -> int | None:
    """
    Return None if allowed, else seconds to wait.
    Tries Redis first, fallback to memory.
    """
    client = await get_redis()
    if client is not None:
        try:
            # per-hour counter
            key = f"ratelimit:{user_id}:hour"
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, 3600)
            else:
                ttl = await client.ttl(key)
                # if ttl == -1, set expire
                if ttl == -1:
                    await client.expire(key, 3600)
            if count > max_per_hour:
                ttl = await client.ttl(key)
                return max(1, int(ttl) if ttl and ttl > 0 else 3600)

            # anti-spam 2s throttle via separate key
            spam_key = f"ratelimit:{user_id}:spam"
            # use SET NX with expire to detect rapid calls
            # we store last timestamp as value
            exists = await client.get(spam_key)
            if exists is not None:
                # exists means last call within interval
                ttl = await client.ttl(spam_key)
                return max(1, int(ttl) if ttl and ttl > 0 else int(min_interval))
            # set spam lock
            await client.setex(spam_key, int(min_interval), "1")
            return None
        except Exception as e:
            log.warning("redis rate limit failed, using memory: %s", e)

    # fallback memory
    now = time.monotonic()
    hits = _mem_hits.setdefault(user_id, [])
    # prune >1h
    cutoff = now - 3600
    # keep only recent
    hits[:] = [t for t in hits if t > cutoff]
    if len(hits) >= max_per_hour:
        wait = int(3600 - (now - hits[0])) + 1
        return max(1, wait)
    # anti-spam 2s check: need separate tracking of last
    # we reuse hits list's last entry for interval check? better track last separately
    # Use a separate dict for last msg time
    # store in a hidden dict (reuse _mem_hits with special handling)
    # We'll use a module global for last msg
    global _mem_last_msg
    try:
        _mem_last_msg  # type: ignore
    except NameError:
        _mem_last_msg = {}  # type: ignore

    last = _mem_last_msg.get(user_id, 0)  # type: ignore
    if now - last < min_interval:
        return int(min_interval - (now - last)) + 1
    return None


async def record_hit(user_id: int) -> None:
    client = await get_redis()
    if client is not None:
        # already counted via INCR above, but for consistency if not counted, ensure incr
        # We already incremented in is_rate_limited, so no-op. For memory fallback we need to record.
        # To avoid double-count, we only record for memory path.
        # Detect: if redis available, hits already tracked.
        return
    now = time.monotonic()
    hits = _mem_hits.setdefault(user_id, [])
    hits.append(now)
    try:
        _mem_last_msg[user_id] = now  # type: ignore
    except NameError:
        pass


# ---------------------------------------------------------------------------
# Queue abstraction (arq enqueue with fallback)
# ---------------------------------------------------------------------------

async def enqueue_download(
    url: str,
    quality: str,
    chat_id: int,
    status_message_id: int,
    user_id: int,
) -> bool:
    """
    Try to enqueue via arq Redis queue. Return True if enqueued, False if Redis unavailable
    and caller should do direct download.
    """
    client = await get_redis()
    if client is None:
        return False
    try:
        from arq.connections import RedisSettings, create_pool

        s = get_settings()
        pool = await create_pool(RedisSettings.from_dsn(s.redis_url))
        try:
            job = await pool.enqueue_job(
                "download_job",
                url,
                quality,
                chat_id,
                status_message_id,
                user_id,
            )
            log.info("enqueued arq job=%s url=%s quality=%s", job.job_id if job else "?", url[:60], quality)
            return True
        finally:
            await pool.close()
    except Exception as e:
        log.warning("arq enqueue failed, fallback to direct: %s", e)
        return False


# Generic helpers for stats
async def incr_stat(key: str, amount: int = 1) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.incrby(f"stats:{key}", amount)
            # also daily key
            import datetime

            today = datetime.date.today().isoformat()
            await client.incrby(f"stats:{key}:{today}", amount)
            await client.expire(f"stats:{key}:{today}", 86400 * 7)
        except Exception:
            pass


async def get_stat(key: str) -> int:
    client = await get_redis()
    if client is not None:
        try:
            val = await client.get(f"stats:{key}")
            return int(val) if val else 0
        except Exception:
            return 0
    return 0
