from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings


def create_bot() -> Bot:
    s = get_settings()
    # Local Bot API Server support for 2GB files
    if s.use_local_bot_api and s.local_bot_api_url:
        # aiogram supports custom base URL via Bot(token, session) hacking
        # We set base_url via bot.api_url if available, else override via make_request
        from aiogram.client.telegram import TelegramAPIServer

        # parse local api url
        server = TelegramAPIServer.from_base(s.local_bot_api_url, is_local=True)
        return Bot(token=s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), server=server)
    return Bot(token=s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    from app.bot.handlers import admin, callback, url_handler
    from app.bot.handlers import start as start_h

    dp = Dispatcher()
    dp.include_router(start_h.router)
    dp.include_router(admin.router)
    dp.include_router(url_handler.router)
    dp.include_router(callback.router)
    return dp
