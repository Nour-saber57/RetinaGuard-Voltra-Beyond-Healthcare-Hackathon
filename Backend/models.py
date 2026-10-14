from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254))

    image_path: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), default="new"
    )

    prediction: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )