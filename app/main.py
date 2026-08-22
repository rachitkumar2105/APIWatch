import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import crud
from app.database import Base, engine, get_db
from app.models import TaskStatus
from app.schemas import TaskCreate, TaskList, TaskRead, TaskUpdate

logger = logging.getLogger("apiwatch")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="APIWatch", description="Task management REST API")

# Explicit allow-list, not "*" — configurable via CORS_ORIGINS for real deployments.
# A production deployment would narrow this to the actual frontend origin(s).
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces or raw DB errors to the client — log server-side, return generic 500.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.post("/tasks", response_model=TaskRead, status_code=201)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    return crud.create_task(db, task)


@app.get("/tasks", response_model=TaskList)
def list_tasks(
    status: Optional[TaskStatus] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = crud.list_tasks(db, status, limit, offset)
    total = crud.count_tasks(db, status)
    return TaskList(items=items, total=total, limit=limit, offset=offset)


@app.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    db_task = crud.get_task(db, task_id)
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return db_task


@app.put("/tasks/{task_id}", response_model=TaskRead)
def update_task(task_id: int, task: TaskUpdate, db: Session = Depends(get_db)):
    db_task = crud.update_task(db, task_id, task)
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return db_task


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_task(db, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
