import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, crud
from app.crud import VersionConflict
from app.database import Base, engine, get_db
from app.models import TaskStatus, User
from app.schemas import TaskCreate, TaskList, TaskRead, TaskUpdate, Token, UserCreate, UserRead

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


limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

Instrumentator().instrument(app).expose(app)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces or raw DB errors to the client — log server-side, return generic 500.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error
    user_id = auth.decode_access_token(token)
    if user_id is None:
        raise credentials_error
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_error
    return user


# ==================== Auth ====================


@app.post("/auth/register", response_model=UserRead, status_code=201)
@limiter.limit("10/minute")
def register(request: Request, user: UserCreate, db: Session = Depends(get_db)):
    existing = crud.get_user_by_username(db, user.username)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already registered")
    hashed = auth.hash_password(user.password)
    try:
        return crud.create_user(db, user, hashed)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already registered")


@app.post("/auth/login", response_model=Token)
@limiter.limit("10/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = crud.get_user_by_username(db, form_data.username)
    if user is None or not auth.verify_password(form_data.password, user.hashed_password):
        # Same error for "no such user" and "wrong password" — deliberately, so a caller
        # can't use this endpoint to enumerate which usernames exist.
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token = auth.create_access_token(subject=str(user.id))
    return Token(access_token=access_token)


# ==================== Health ====================


@app.get("/health/live")
def health_live():
    """Liveness: is the process itself up and responding. Never touches the
    database — if this fails, the process needs a restart, not a retry."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    """Readiness: is this instance actually able to serve real traffic right
    now. Runs a trivial query to confirm the database connection is alive
    before reporting ready — a load balancer should stop routing here if this
    fails, without restarting the process."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed: database not reachable")
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}


# ==================== Tasks (all require auth, all scoped to current_user) ====================


@app.post("/tasks", response_model=TaskRead, status_code=201)
@limiter.limit("30/minute")
def create_task(
    request: Request,
    task: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key:
        cached = crud.get_idempotent_response(db, idempotency_key, current_user.id)
        if cached is not None:
            body, status_code = cached
            return JSONResponse(status_code=status_code, content=body)

    db_task = crud.create_task(db, task, current_user.id)
    result = TaskRead.model_validate(db_task)

    if idempotency_key:
        crud.save_idempotent_response(
            db, idempotency_key, current_user.id, result.model_dump(mode="json"), 201
        )
    return db_task


@app.get("/tasks", response_model=TaskList)
def list_tasks(
    status: Optional[TaskStatus] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = crud.list_tasks(db, current_user.id, status, limit, offset)
    total = crud.count_tasks(db, current_user.id, status)
    return TaskList(items=items, total=total, limit=limit, offset=offset)


@app.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_task = crud.get_task(db, task_id, current_user.id)
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return db_task


@app.put("/tasks/{task_id}", response_model=TaskRead)
@limiter.limit("30/minute")
def update_task(
    request: Request,
    task_id: int,
    task: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        db_task = crud.update_task(db, task_id, task, current_user.id)
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return db_task


@app.delete("/tasks/{task_id}", status_code=204)
@limiter.limit("30/minute")
def delete_task(
    request: Request,
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = crud.delete_task(db, task_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
