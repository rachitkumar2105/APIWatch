import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Integer, String

from app.database import Base


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    # Indexed: GET /tasks filters by status and the natural sort/scan order is by created_at.
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.pending, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
