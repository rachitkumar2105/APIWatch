import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app import config  # noqa: F401  (loads .env before DATABASE_URL is read)


def normalize_database_url(url: str) -> str:
    """Hosted Postgres providers (Heroku, Render, some Neon snippets) hand out
    'postgres://' URLs, which SQLAlchemy 2.x rejects. Rewrite to 'postgresql://'."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a file-based SQLite database, so a path
    like sqlite:////home/user/app/data/apiwatch.db works on a fresh container."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    database = parsed.database
    if not database or database == ":memory:":
        return
    directory = os.path.dirname(os.path.abspath(database))
    os.makedirs(directory, exist_ok=True)


def engine_options(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    # Managed Postgres (Neon, Supabase) closes idle connections; pre-ping and
    # recycle so a pooled connection that was dropped is replaced, not reused.
    return {"pool_pre_ping": True, "pool_recycle": 300}


# "or": a variable that exists but is empty (common in hosting dashboards) means "unset".
DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL") or "sqlite:///./apiwatch.db")
ensure_sqlite_directory(DATABASE_URL)

engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
