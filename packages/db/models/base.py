from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = sa.MetaData()


UUIDPrimaryKey = Annotated[
    uuid.UUID,
    mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4),
]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
        onupdate=sa.func.now(),
    )
