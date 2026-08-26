from __future__ import annotations

from sqlalchemy import func, select

from app.models.user import User
from app.services.db import get_session_factory


async def get_user(user_id: int) -> User | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(User, user_id)


async def upsert_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    lang: str | None = None,
) -> User:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user:
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            if lang is not None:
                user.lang = lang
            # last_seen auto via onupdate, but also increment?
            await session.commit()
            await session.refresh(user)
            return user
        else:
            user = User(
                id=user_id,
                username=username,
                first_name=first_name,
                lang=lang or "en",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user


async def set_user_quality(user_id: int, quality: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user:
            user.default_quality = quality
            await session.commit()
        else:
            # create user with quality
            user = User(id=user_id, default_quality=quality)
            session.add(user)
            await session.commit()


async def set_user_lang(user_id: int, lang: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user:
            user.lang = lang
            await session.commit()
        else:
            user = User(id=user_id, lang=lang)
            session.add(user)
            await session.commit()


async def increment_download_count(user_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user:
            user.download_count = (user.download_count or 0) + 1
            await session.commit()


async def get_user_lang(user_id: int) -> str:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user and user.lang:
            return user.lang
        return "en"


async def get_user_quality(user_id: int) -> str:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user and user.default_quality:
            return user.default_quality
        return "best"


async def count_users() -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(func.count()).select_from(User))
        return result.scalar() or 0


async def total_downloads() -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(func.sum(User.download_count)))
        val = result.scalar()
        return int(val) if val else 0
