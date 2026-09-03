from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

import structlog
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bot import create_bot, create_dispatcher
from app.bot.middlewares.db import UserMiddleware
from app.bot.middlewares.i18n import I18nMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.config import get_settings
from app.core.cleanup import cleanup_loop
from app.services.db import init_db

log = structlog.get_logger()

# Track uptime for / and /uptime endpoints (used by Render / UptimeRobot)
_START_TIME = time.monotonic()


def _get_effective_port(s) -> int:
    """Resolve port for Render/Railway/Koyeb: prefer $PORT, fallback to s.effective_port / webapp_port."""
    # s.effective_port already handles PORT via Settings.port
    try:
        if hasattr(s, "effective_port"):
            return int(s.effective_port)  # type: ignore
    except Exception:
        pass
    port_env = os.getenv("PORT")
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass
    return int(s.webapp_port)


def setup_logging():
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer() if s.use_webhook else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
    # Sentry (Phase-5 observability) — optional, no extra dep required; uses stdlib if DSN set
    if s.sentry_dsn:
        try:
            import sentry_sdk  # type: ignore

            sentry_sdk.init(dsn=s.sentry_dsn, traces_sample_rate=0.1)
            log.info("sentry_enabled")
        except ImportError:
            log.warning("sentry_dsn set but sentry-sdk not installed — pip install sentry-sdk")
        except Exception as e:
            log.warning("sentry_init_failed", error=str(e))


async def _ytdlp_update_loop(interval_hours: int):
    """Periodic yt-dlp updater — keeps bot working as yt-dlp fixes Twitter/IG etc."""
    import subprocess
    import sys

    interval = max(1, interval_hours) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            log.info("ytdlp_periodic_update_check")
            for args in (
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q"],
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q", "--user"],
            ):
                res = subprocess.run(args, timeout=90, capture_output=True)
                if res.returncode == 0:
                    break
                err = res.stderr.decode().lower() if res.stderr else ""
                if "permission" not in err and "could not install" not in err:
                    break
            if res.returncode == 0:
                try:
                    import importlib
                    import yt_dlp

                    importlib.reload(yt_dlp)
                    log.info("ytdlp_periodic_update_done", version=getattr(yt_dlp.version, "__version__", "unknown"))
                except Exception:
                    pass
            else:
                log.warning("ytdlp_periodic_update_failed", returncode=res.returncode)
        except Exception as e:
            log.warning("ytdlp_periodic_update_error", error=str(e))


async def on_startup(bot: Bot):
    s = get_settings()
    # init DB
    try:
        await init_db()
        log.info("db_initialized", db=s.database_url.split("@")[-1])
    except Exception as e:
        log.warning("db_init_failed", error=str(e))

    # init Redis (test connection, graceful fallback)
    try:
        from app.services.redis import get_redis

        r = await get_redis()
        if r is not None:
            log.info("redis_ready", url=s.redis_url.split("@")[-1])
        else:
            log.info("redis_disabled - using in-memory fallbacks")
    except Exception as e:
        log.warning("redis_init_failed", error=str(e))

    # webhook setup
    if s.use_webhook and s.webhook_url:
        await bot.set_webhook(
            url=s.webhook_url,
            secret_token=s.webhook_secret,
            drop_pending_updates=True,
        )
        log.info("webhook_set", url=s.webhook_url)
    else:
        # polling mode - delete webhook
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("polling_mode - webhook deleted")

    # yt-dlp version + auto-update (Phase-5 fix: always fresh)
    try:
        import yt_dlp

        log.info("yt_dlp_version", version=yt_dlp.version.__version__)
        if s.ytdlp_auto_update:
            # Run update - handles docker bot user permission via --user fallback
            try:
                import subprocess
                import sys

                log.info("ytdlp_auto_update_start")
                for args in (
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q"],
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "--no-cache-dir", "-q", "--user"],
                ):
                    res = subprocess.run(args, timeout=90, capture_output=True)
                    if res.returncode == 0:
                        break
                    # If permission error, try --user next iteration
                    err = res.stderr.decode().lower() if res.stderr else ""
                    if "permission" not in err and "could not install" not in err:
                        break
                if res.returncode == 0:
                    import importlib

                    try:
                        importlib.reload(yt_dlp)
                    except Exception:
                        pass
                    log.info("ytdlp_auto_update_done", version=getattr(yt_dlp.version, "__version__", "unknown"))
                else:
                    log.warning("ytdlp_auto_update_failed", stderr=res.stderr.decode()[:200] if res.stderr else "unknown")
            except Exception as e:
                log.warning("ytdlp_auto_update_failed", error=str(e))
    except Exception:
        pass

    # Start periodic auto-update loop (every N hours) if enabled
    if s.ytdlp_auto_update:
        try:
            asyncio.create_task(_ytdlp_update_loop(s.ytdlp_auto_update_interval_hours))
            log.info("ytdlp_auto_update_loop_started", interval_hours=s.ytdlp_auto_update_interval_hours)
        except Exception as e:
            log.warning("ytdlp_loop_start_failed", error=str(e))


async def on_shutdown(bot: Bot):
    log.info("shutdown")
    try:
        from app.services.redis import close_redis

        await close_redis()
    except Exception:
        pass
    try:
        await bot.session.close()
    except Exception:
        pass


def build_app() -> tuple[Bot, Dispatcher, web.Application | None]:
    s = get_settings()
    bot = create_bot()
    dp = create_dispatcher()

    # middlewares - order matters: UserMiddleware first to upsert, then rate/i18n
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())
    dp.message.middleware(I18nMiddleware())
    dp.callback_query.middleware(I18nMiddleware())

    async def _startup_wrapper():
        await on_startup(bot)

    async def _shutdown_wrapper():
        await on_shutdown(bot)

    dp.startup.register(_startup_wrapper)
    dp.shutdown.register(_shutdown_wrapper)

    # Always build a health/keepalive app — required for Render / UptimeRobot.
    # Previously health was only added when enable_metrics=True, so UptimeRobot
    # pinging "/" or "/health" could get 404 when metrics were disabled.
    # Now "/" , "/ping" , "/health" always return 200, even in polling mode.
    metrics_app: web.Application = web.Application()

    # --- keepalive / root handlers (fix UptimeRobot 404 on Render free tier) ---
    async def root(request):  # type: ignore
        uptime_s = int(time.monotonic() - _START_TIME)
        # Support both JSON and plain-text checks (UptimeRobot keyword monitoring)
        accept = request.headers.get("Accept", "")
        if "text/html" in accept:
            return web.Response(
                text=f"<html><body><h1>UniMedia Bot is running</h1><p>Uptime: {uptime_s}s</p><p><a href='/health'>/health</a> · <a href='/metrics'>/metrics</a></p></body></html>",
                content_type="text/html",
            )
        return web.json_response(
            {"status": "ok", "bot": "online", "service": "unimedia-downloader", "uptime_seconds": uptime_s}
        )

    async def ping(request):  # type: ignore
        # UptimeRobot loves /ping returning 200 + pong
        return web.json_response({"status": "ok", "ping": "pong"})

    async def alive(request):  # type: ignore
        return web.Response(text="OK", content_type="text/plain")

    async def health(request):  # type: ignore
        return web.json_response({"status": "ok", "bot": "online"})

    async def health_detailed(request):  # type: ignore
        details = {"status": "ok"}
        try:
            from sqlalchemy import text as sa_text

            from app.services.db import get_engine

            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(sa_text("SELECT 1"))
            details["db"] = "ok"
        except Exception as e:
            details["db"] = f"error: {e}"
            details["status"] = "degraded"
        try:
            from app.services.redis import get_redis

            r = await get_redis()
            if r:
                await r.ping()
                details["redis"] = "ok"
            else:
                details["redis"] = "disabled (in-memory fallback)"
        except Exception as e:
            details["redis"] = f"error: {e}"
        status = 200 if details["status"] == "ok" else 503
        return web.json_response(details, status=status)

    # Prometheus-style metrics (Phase-5 observability)
    async def metrics(request):  # type: ignore
        # JSON metrics + Prometheus text fallback via Accept header
        try:
            from app.services.job_service import count_jobs, total_jobs
            from app.services.redis import get_stat
            from app.services.user_service import count_users, total_downloads

            users = await count_users()
            downloads = await total_downloads()
            jobs_total = await total_jobs()
            jobs_done = await count_jobs("done")
            jobs_failed = await count_jobs("failed")
            jobs_queued = await count_jobs("queued")
            redis_success = await get_stat("downloads_success")
            redis_failed = await get_stat("downloads_failed")
            # disk usage of download dir
            try:
                import shutil

                du = shutil.disk_usage(str(s.download_dir))
                disk_free_mb = du.free // (1024 * 1024)
                disk_total_mb = du.total // (1024 * 1024)
            except Exception:
                disk_free_mb = disk_total_mb = 0
            data = {
                "users": users,
                "downloads_db": downloads,
                "jobs_total": jobs_total,
                "jobs_done": jobs_done,
                "jobs_failed": jobs_failed,
                "jobs_queued": jobs_queued,
                "redis_success": redis_success,
                "redis_failed": redis_failed,
                "disk_free_mb": disk_free_mb,
                "disk_total_mb": disk_total_mb,
                "threshold_bytes": s.large_file_threshold_bytes,
            }
            # If Prometheus requested, render text
            accept = request.headers.get("Accept", "")
            if "text/plain" in accept:
                lines = []
                for k, v in data.items():
                    lines.append(f"# HELP unimedia_{k} {k}")
                    lines.append(f"# TYPE unimedia_{k} gauge")
                    lines.append(f"unimedia_{k} {v}")
                return web.Response(text="\n".join(lines), content_type="text/plain")
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # Register keepalive routes — cover every path UptimeRobot/Render might ping.
    # aiohttp's add_get automatically handles HEAD, so only GET needed (avoids duplicate HEAD error).
    for path, handler in [
        ("/", root),
        ("/ping", ping),
        ("/alive", alive),
        ("/uptime", alive),
        ("/health", health),
        ("/healthz", health_detailed),
        ("/status", health),
    ]:
        metrics_app.router.add_get(path, handler)

    if s.enable_metrics:
        metrics_app.router.add_get("/metrics", metrics)

    if s.use_webhook and s.webhook_url:
        # Use metrics_app as base so keepalive routes are preserved in webhook mode
        app = metrics_app
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=s.webhook_secret).register(
            app, path="/webhook"
        )
        setup_application(app, dp, bot=bot)
        return bot, dp, app
    # Polling mode: always return health app for background serving (fixes Render 404)
    return bot, dp, metrics_app


async def polling_main():
    s = get_settings()
    bot, dp, metrics_app = build_app()
    # background cleanup with proper lifecycle
    cleanup_task = asyncio.create_task(cleanup_loop())
    # health/keepalive server (Phase-5) — even in polling mode, expose /health /metrics
    # Fix Render free tier: bind to $PORT if set, not just WEBAPP_PORT, and always start server
    metrics_runner = None
    if metrics_app is not None:
        try:
            port = _get_effective_port(s)
            metrics_runner = web.AppRunner(metrics_app)
            await metrics_runner.setup()
            site = web.TCPSite(metrics_runner, host=s.webapp_host, port=port)
            await site.start()
            log.info("health_server_started", host=s.webapp_host, port=port, mode="polling")
            log.info("keepalive_endpoints", endpoints="/, /ping, /health, /healthz, /metrics")
        except OSError as e:
            log.warning("health_server_failed", error=str(e))
            metrics_runner = None
    log.info("starting_polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        if metrics_runner:
            try:
                await metrics_runner.cleanup()
            except Exception:
                pass
        await on_shutdown(bot)


async def webhook_main():
    s = get_settings()
    bot, _dp, app = build_app()
    assert app is not None
    cleanup_task = asyncio.create_task(cleanup_loop())
    runner = web.AppRunner(app)
    await runner.setup()
    port = _get_effective_port(s)
    site = web.TCPSite(runner, host=s.webapp_host, port=port)
    log.info("starting_webhook", host=s.webapp_host, port=port, webhook=s.webhook_url)
    await site.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await on_shutdown(bot)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--polling", action="store_true", help="force polling even if webhook enabled")
    parser.add_argument("--webhook", action="store_true", help="force webhook")
    args = parser.parse_args()

    setup_logging()
    s = get_settings()
    s.ensure_dirs()

    # CLI overrides
    if args.polling:
        s.use_webhook = False
    if args.webhook:
        s.use_webhook = True

    try:
        if s.use_webhook and not args.polling:
            asyncio.run(webhook_main())
        else:
            asyncio.run(polling_main())
    except KeyboardInterrupt:
        log.info("interrupted")


if __name__ == "__main__":
    main()
