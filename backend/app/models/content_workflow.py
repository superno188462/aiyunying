from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.core.time import shanghai_now


class ContentWorkflow(Base):
    """持久化一次从热门参考到双平台发布包的人工审核流程。"""

    __tablename__ = "content_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_accounts.id"), nullable=True)
    topic: Mapped[str] = mapped_column(String(120))
    stage: Mapped[str] = mapped_column(String(32), default="created", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    references: Mapped[list] = mapped_column(JSON, default=list)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    selected_topic: Mapped[dict] = mapped_column(JSON, default=dict)
    draft_title: Mapped[str] = mapped_column(String(300), default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    draft_versions: Mapped[list] = mapped_column(JSON, default=list)
    cards: Mapped[list] = mapped_column(JSON, default=list)
    xhs_title: Mapped[str] = mapped_column(String(300), default="")
    xhs_caption: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now, onupdate=shanghai_now)
