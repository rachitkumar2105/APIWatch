import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, limiter

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    limiter.reset()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    """A TestClient pre-authenticated as a freshly-registered user, with the
    Authorization header set on every request — for tests that don't care
    about the auth flow itself, just need a logged-in caller."""
    client.post(
        "/auth/register", json={"username": "taskowner", "password": "correct-horse-battery"}
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "taskowner", "password": "correct-horse-battery"},
    )
    token = login_resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def race_env(tmp_path):
    """File-based SQLite (a real connection pool, unlike the shared StaticPool
    connection above) so threads can genuinely interleave. Registers a user and
    exposes that user's auth headers plus a session factory for direct DB checks."""
    # Set RACE_TEST_DATABASE_URL to run these concurrency tests against another database
    # (e.g. PostgreSQL); the default is a throwaway SQLite file.
    url = os.getenv("RACE_TEST_DATABASE_URL")
    if url:
        file_engine = create_engine(url)
    else:
        file_engine = create_engine(
            f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
        )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=file_engine)
    Base.metadata.create_all(bind=file_engine)

    def file_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    previous_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = file_get_db
    limiter.reset()

    setup_client = TestClient(app)
    creds = {"username": "raceuser", "password": "race-password-123"}
    setup_client.post("/auth/register", json=creds)
    token = setup_client.post("/auth/login", data=creds).json()["access_token"]

    yield SimpleNamespace(
        session_factory=session_factory, headers={"Authorization": f"Bearer {token}"}
    )

    app.dependency_overrides[get_db] = previous_override
    Base.metadata.drop_all(bind=file_engine)
    file_engine.dispose()
