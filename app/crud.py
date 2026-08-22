from typing import Optional

from sqlalchemy.orm import Session

from app.models import Task, TaskStatus
from app.schemas import TaskCreate, TaskUpdate


def create_task(db: Session, task: TaskCreate) -> Task:
    db_task = Task(title=task.title, description=task.description, status=task.status)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


def get_task(db: Session, task_id: int) -> Optional[Task]:
    return db.query(Task).filter(Task.id == task_id).first()


def list_tasks(
    db: Session,
    status: Optional[TaskStatus] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Task]:
    query = db.query(Task)
    if status is not None:
        query = query.filter(Task.status == status)
    return query.order_by(Task.created_at.desc()).offset(offset).limit(limit).all()


def count_tasks(db: Session, status: Optional[TaskStatus] = None) -> int:
    query = db.query(Task)
    if status is not None:
        query = query.filter(Task.status == status)
    return query.count()


def update_task(db: Session, task_id: int, task: TaskUpdate) -> Optional[Task]:
    db_task = get_task(db, task_id)
    if db_task is None:
        return None
    db_task.title = task.title
    db_task.description = task.description
    db_task.status = task.status
    db.commit()
    db.refresh(db_task)
    return db_task


def delete_task(db: Session, task_id: int) -> bool:
    db_task = get_task(db, task_id)
    if db_task is None:
        return False
    db.delete(db_task)
    db.commit()
    return True
