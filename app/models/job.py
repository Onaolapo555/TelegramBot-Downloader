from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.services.db import Base


class Job(Base):
    __tablename__ = "jobs"

    # Use Integer for SQLite autoincrement compatibility (works on Postgres too for <2B rows)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    url: Mapped[str] = mapped_column(Text)
    quality: Mapped[str] = mapped_column(String(16), default="best")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/downloading/uploading/done/failed
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
