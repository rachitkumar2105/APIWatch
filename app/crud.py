import json
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey, Task, TaskStatus, User
from app.schemas import TaskCreate, TaskRead, TaskUpdate, UserCreate


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
    Raises VersionConflict if the row's version no longer matches task.version.

    The version check lives in the UPDATE's WHERE clause, so check-and-write is a
    single atomic statement: if another request bumped the version first, this
    UPDATE matches zero rows (rowcount == 0) instead of overwriting their change.
    """
    existing = get_task(db, task_id, owner_id)
    if existing is None:
        return None
    result = db.execute(
        update(Task)
        .where(Task.id == task_id, Task.owner_id == owner_id, Task.version == task.version)
        .values(
            title=task.title,
            description=task.description,
            status=task.status,
            version=Task.version + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        raise VersionConflict(
            f"Task {task_id} was modified by another request; this update was based on "
            f"stale version {task.version}. Re-fetch the task and retry."
        )
    db.commit()
    db.refresh(existing)
    return existing


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


def create_task_with_idempotency_key(
    db: Session, task: TaskCreate, owner_id: int, key: str
) -> tuple[dict, int]:
    """Create the task and record its idempotency key in ONE transaction.

    If another request with the same (owner_id, key) committed first, the unique
    constraint rejects this insert; we roll back (which also discards this
    request's task) and return the winner's stored response instead.
    """
    db_task = Task(
        title=task.title,
        description=task.description,
        status=task.status,
        owner_id=owner_id,
        version=1,
    )
    db.add(db_task)
    try:
        db.flush()
        db.refresh(db_task)
        body = TaskRead.model_validate(db_task).model_dump(mode="json")
        db.add(
            IdempotencyKey(
                key=key,
                owner_id=owner_id,
                response_body=json.dumps(body, default=str),
                status_code=201,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        stored = get_idempotent_response(db, key, owner_id)
        if stored is None:
            raise
        return stored
    return body, 201
