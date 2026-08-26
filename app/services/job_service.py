from __future__ import annotations

from sqlalchemy import desc, func, select

from app.models.job import Job
from app.services.db import get_session_factory


async def create_job(user_id: int, chat_id: int, url: str, quality: str) -> Job:
    factory = get_session_factory()
    async with factory() as session:
        job = Job(user_id=user_id, chat_id=chat_id, url=url, quality=quality, status="queued")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def update_job_status(job_id: int, status: str, error: str | None = None, file_path: str | None = None) -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = status
            if error is not None:
                job.error = error[:2000] if error else None
            if file_path is not None:
                job.file_path = file_path
            await session.commit()


async def count_jobs(status: str | None = None) -> int:
    factory = get_session_factory()
    async with factory() as session:
        if status:
            result = await session.execute(select(func.count()).select_from(Job).where(Job.status == status))
        else:
            result = await session.execute(select(func.count()).select_from(Job))
        return result.scalar() or 0


async def recent_jobs(limit: int = 5) -> list[Job]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Job).order_by(desc(Job.created_at)).limit(limit))
        return list(result.scalars().all())


async def total_jobs() -> int:
    return await count_jobs()
