from __future__ import annotations

import argparse
import asyncio
import logging
import sys

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

    # update yt-dlp? (optional - log version)
    try:
        import yt_dlp
        log.info("yt_dlp_version", version=yt_dlp.version.__version__)
    except Exception:
        pass


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

    if s.use_webhook and s.webhook_url:
        app = web.Application()
        # webhook handler at /webhook
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=s.webhook_secret).register(
            app, path="/webhook"
        )
        setup_application(app, dp, bot=bot)
        # healthchecks
        async def health(request):  # type: ignore
            return web.json_response({"status": "ok", "bot": "online"})

        async def health_detailed(request):  # type: ignore
            # detailed health with DB/Redis checks
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

        app.router.add_get("/health", health)
        app.router.add_get("/healthz", health_detailed)
        return bot, dp, app
    return bot, dp, None


async def polling_main():
    get_settings()
    bot, dp, _ = build_app()
    # background cleanup with proper lifecycle
    cleanup_task = asyncio.create_task(cleanup_loop())
    log.info("starting_polling")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await on_shutdown(bot)


async def webhook_main():
    s = get_settings()
    bot, _dp, app = build_app()
    assert app is not None
    cleanup_task = asyncio.create_task(cleanup_loop())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=s.webapp_host, port=s.webapp_port)
    log.info("starting_webhook", host=s.webapp_host, port=s.webapp_port, webhook=s.webhook_url)
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
