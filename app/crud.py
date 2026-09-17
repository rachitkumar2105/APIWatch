import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models import IdempotencyKey, Task, TaskStatus, User
from app.schemas import TaskCreate, TaskUpdate, UserCreate


class VersionConflict(Exception):
    """Raised when a PUT's supplied version doesn't match the current row --
    someone else updated this task since the client last read it."""


# ---------------- Users ----------------


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, user: UserCreate, hashed_password: str) -> User:
    db_user = User(username=user.username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


# ---------------- Tasks (all scoped to owner_id) ----------------


def create_task(db: Session, task: TaskCreate, owner_id: int) -> Task:
    db_task = Task(
        title=task.title,
        description=task.description,
        status=task.status,
        owner_id=owner_id,
        version=1,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def get_task(db: Session, task_id: int, owner_id: int) -> Optional[Task]:
    return db.query(Task).filter(Task.id == task_id, Task.owner_id == owner_id).first()


def list_tasks(
    db: Session,
    owner_id: int,
    status: Optional[TaskStatus] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Task]:
    query = db.query(Task).filter(Task.owner_id == owner_id)
    if status is not None:
        query = query.filter(Task.status == status)
    return query.order_by(Task.created_at.desc()).offset(offset).limit(limit).all()


def count_tasks(db: Session, owner_id: int, status: Optional[TaskStatus] = None) -> int:
    query = db.query(Task).filter(Task.owner_id == owner_id)
    if status is not None:
        query = query.filter(Task.status == status)
    return query.count()


def update_task(db: Session, task_id: int, task: TaskUpdate, owner_id: int) -> Optional[Task]:
    """Returns None if not found (or not owned by this user).
    Raises VersionConflict if task.version doesn't match the row's current version.
    """
    db_task = get_task(db, task_id, owner_id)
    if db_task is None:
        return None
    if db_task.version != task.version:
        raise VersionConflict(
            f"Task {task_id} is at version {db_task.version}, but update was based on "
            f"version {task.version}. Re-fetch the task and retry."
        )
    db_task.title = task.title
    db_task.description = task.description
    db_task.status = task.status
    db_task.version += 1
    db.commit()
    db.refresh(db_task)
    return db_task


def delete_task(db: Session, task_id: int, owner_id: int) -> bool:
    db_task = get_task(db, task_id, owner_id)
    if db_task is None:
        return False
    db.delete(db_task)
    db.commit()
    return True


# ---------------- Idempotency ----------------


def get_idempotent_response(db: Session, key: str, owner_id: int) -> Optional[tuple[dict, int]]:
    record = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.key == key, IdempotencyKey.owner_id == owner_id)
        .first()
    )
    if record is None:
        return None
    return json.loads(record.response_body), record.status_code


def save_idempotent_response(
    db: Session, key: str, owner_id: int, response_body: dict, status_code: int
) -> None:
    record = IdempotencyKey(
        key=key,
        owner_id=owner_id,
        response_body=json.dumps(response_body, default=str),
        status_code=status_code,
    )
    db.add(record)
    db.commit()
