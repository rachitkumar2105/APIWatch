import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tasks = relationship("Task", back_populates="owner", cascade="all, delete-orphan")


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
    # Optimistic-locking version. Every update must supply the version it read;
    # a stale version means someone else updated the task first -- reject with 409
    # instead of silently overwriting their change (last-write-wins).
    version = Column(Integer, nullable=False, default=1)

    # Tasks are scoped to the user that created them -- every query in crud.py
    # filters by owner_id, so one user can never see or modify another's tasks.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    owner = relationship("User", back_populates="tasks")


class IdempotencyKey(Base):
    """Records a client-supplied Idempotency-Key for POST /tasks so a retried
    request (e.g. after a client-side timeout) returns the ORIGINAL response
    instead of creating a duplicate task. Scoped per-user: two different users
    could reuse the same key string without colliding.
    """

    __tablename__ = "idempotency_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    response_body = Column(String, nullable=False)  # JSON-serialized TaskRead
    status_code = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
