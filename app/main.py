from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.bot import create_bot, create_dispatcher
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.bot.middlewares.i18n import I18nMiddleware
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


def build_app() -> tuple[Bot, Dispatcher, web.Application | None]:
    s = get_settings()
    bot = create_bot()
    dp = create_dispatcher()

    # middlewares
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())
    dp.message.middleware(I18nMiddleware())
    dp.callback_query.middleware(I18nMiddleware())

    dp.startup.register(lambda: on_startup(bot))
    dp.shutdown.register(lambda: on_shutdown(bot))

    if s.use_webhook and s.webhook_url:
        app = web.Application()
        # webhook handler at /webhook
        # aiogram expects path = webhook path; we use /webhook
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=s.webhook_secret).register(
            app, path="/webhook"
        )
        setup_application(app, dp, bot=bot)
        # healthcheck
        async def health(request):  # type: ignore
            return web.json_response({"status": "ok"})
        app.router.add_get("/health", health)
        return bot, dp, app
    return bot, dp, None


async def polling_main():
    s = get_settings()
    bot, dp, _ = build_app()
    # background cleanup
    asyncio.create_task(cleanup_loop())
    log.info("starting_polling")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def webhook_main():
    s = get_settings()
    bot, dp, app = build_app()
    assert app is not None
    # background cleanup
    asyncio.create_task(cleanup_loop())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=s.webapp_host, port=s.webapp_port)
    log.info("starting_webhook", host=s.webapp_host, port=s.webapp_port, webhook=s.webhook_url)
    await site.start()
    # keep alive
    while True:
        await asyncio.sleep(3600)


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
