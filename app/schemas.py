from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models import TaskStatus

TITLE_MAX = 200
DESCRIPTION_MAX = 2000


class TaskCreate(BaseModel):
    # Leading/trailing whitespace is stripped before validation, so a title of
    # only spaces fails min_length instead of saving an invisible task.
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: Optional[str] = Field(default=None, max_length=DESCRIPTION_MAX)
    status: TaskStatus = TaskStatus.pending


class TaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: Optional[str] = Field(default=None, max_length=DESCRIPTION_MAX)
    status: TaskStatus
    # Optimistic-locking token. The client must send back the version it last
    # read; a mismatch means someone else updated the task first (409 Conflict)
    # rather than the server silently overwriting their change.
    version: int = Field(ge=1)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    version: int


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int
    limit: int
    offset: int


# ---- Auth ----


Username = Annotated[
    str,
    # Letters, digits, dot, dash and underscore only: no spaces, markup or
    # control characters ever reach the database or a client's screen.
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$"
    ),
]


class UserCreate(BaseModel):
    username: Username
    # Passwords are taken exactly as typed: never stripped or normalised.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        # bcrypt reads at most 72 bytes; multi-byte characters can exceed that
        # even within 72 characters, so check the encoded length too.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
