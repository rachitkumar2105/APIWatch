from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models import TaskStatus


class TaskCreate(BaseModel):
    title: str = Field(min_length=1)
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.pending


class TaskUpdate(BaseModel):
    title: str = Field(min_length=1)
    description: Optional[str] = None
    status: TaskStatus


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int
    limit: int
    offset: int
