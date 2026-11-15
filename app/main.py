import hmac
import ipaddress
import logging
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, config, crud
from app.crud import VersionConflict
from app.database import Base, engine, get_db
from app.models import TaskStatus, User
from app.schemas import TaskCreate, TaskList, TaskRead, TaskUpdate, Token, UserCreate, UserRead

logger = logging.getLogger("apiwatch")

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="APIWatch",
    version=config.APP_VERSION,
    description=(
        "Task management REST API with JWT auth, idempotent creates, optimistic "
        "locking, rate limits and Prometheus metrics.\n\n"
        "To try it here: `POST /auth/register`, then use the **Authorize** button "
        "with the same username and password."
    ),
)


# ==================== Middleware ====================


class SecurityHeadersMiddleware:
    """Adds baseline security headers to every response (pure ASGI, so it
    doesn't buffer streaming responses the way BaseHTTPMiddleware does).

    The API only returns JSON, so its CSP forbids loading anything at all.
    The Swagger/ReDoc pages need their CDN assets, so they only get the
    framing rule. frame-ancestors allows huggingface.co because the Space
    page embeds the app in an iframe.
    """

    DOCS_PATHS = ("/docs", "/redoc")
    FRAME_ANCESTORS = "frame-ancestors 'self' https://huggingface.co"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_docs = scope["path"].startswith(self.DOCS_PATHS)
        csp = self.FRAME_ANCESTORS if is_docs else f"default-src 'none'; {self.FRAME_ANCESTORS}"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", csp.encode()),
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)

# Explicit allow-list (plus an optional regex for preview deployments), never "*".
# Bearer tokens travel in the Authorization header, not cookies, so credentials
# mode stays off.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_origin_regex=config.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    max_age=600,
)


def _is_public_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    """Rate-limit key.

    Behind a trusted proxy (TRUST_PROXY_HEADERS=true) the TCP peer is the proxy,
    so every visitor would share one bucket. Instead, walk X-Forwarded-For from
    the right (entries appended by the proxies themselves) and take the first
    public address. A client can prepend fake entries on the left, but can't
    get past the address the edge proxy appended for it.
    """
    peer = request.client.host if request.client else "unknown"
    if not config.TRUST_PROXY_HEADERS:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        if _is_public_ip(hop):
            return hop
    return peer


limiter = Limiter(key_func=client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces or raw DB errors to the client: log server-side, return generic 500.
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
    if user_id is None or not user_id.isdigit():
        raise credentials_error
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_error
    return user


# ==================== Service ====================


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root():
    """The Hugging Face Space page opens '/', so send people to the interactive docs."""
    return RedirectResponse(url="/docs")


@app.get("/metrics", include_in_schema=False)
def metrics(authorization: Optional[str] = Header(default=None)):
    """Prometheus scrape endpoint.

    - METRICS_TOKEN set: requires 'Authorization: Bearer <token>' (constant-time compare).
    - Not set, production: not served (404), so a public deployment doesn't
      advertise its traffic shape by default.
    - Not set, development: open, as the local Prometheus/Grafana stack expects.
    """
    token = config.METRICS_TOKEN
    if token is None:
        if config.IS_PRODUCTION:
            raise HTTPException(status_code=404, detail="Not Found")
    elif not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(
            status_code=401,
            detail="Metrics require a bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


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
        # Same error for "no such user" and "wrong password", deliberately, so a caller
        # can't use this endpoint to enumerate which usernames exist.
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token = auth.create_access_token(subject=str(user.id))
    return Token(access_token=access_token, expires_in=auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60)


@app.get("/auth/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)):
    """The account behind the bearer token. Lets a client check a stored token is still valid."""
    return current_user


# ==================== Health ====================


@app.get("/health/live")
@app.head("/health/live", include_in_schema=False)
def health_live():
    """Liveness: is the process itself up and responding. Never touches the
    database; if this fails, the process needs a restart, not a retry."""
    return {"status": "ok"}


@app.get("/health/ready")
@app.head("/health/ready", include_in_schema=False)
def health_ready(db: Session = Depends(get_db)):
    """Readiness: is this instance actually able to serve real traffic right
    now. Runs a trivial query to confirm the database connection is alive
    before reporting ready; a load balancer should stop routing here if this
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
    idempotency_key: Optional[str] = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
):
    if idempotency_key:
        cached = crud.get_idempotent_response(db, idempotency_key, current_user.id)
        if cached is None:
            cached = crud.create_task_with_idempotency_key(
                db, task, current_user.id, idempotency_key
            )
        body, status_code = cached
        return JSONResponse(status_code=status_code, content=body)

    return crud.create_task(db, task, current_user.id)


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
