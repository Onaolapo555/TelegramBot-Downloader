from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.services.db import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_user_id", "user_id"),
        Index("ix_jobs_status", "status"),
        {"sqlite_autoincrement": True},
    )

    # Use Integer for SQLite autoincrement compatibility (works on Postgres too for <2B rows)
    # NOTE: indexes are defined once in __table_args__ below — do NOT also set index=True
    # on the columns (duplicate definitions caused "index already exists" on SQLite create_all).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    url: Mapped[str] = mapped_column(Text)
    quality: Mapped[str] = mapped_column(String(16), default="best")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/downloading/uploading/done/failed
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
