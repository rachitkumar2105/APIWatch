"""Behaviour added to run safely as a public deployment (Hugging Face Spaces + Vercel)."""

import pytest
from starlette.requests import Request

from app import auth, config
from app import main as main_module
from app.database import engine_options, ensure_sqlite_directory, normalize_database_url


def make_request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


class TestRootAndDocs:
    def test_root_redirects_to_docs(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/docs"

    def test_head_works_for_uptime_monitors(self, client):
        assert client.head("/health/live").status_code == 200
        assert client.head("/health/ready").status_code == 200
        assert client.head("/", follow_redirects=False).status_code == 307

    def test_openapi_reports_version(self, client):
        assert client.get("/openapi.json").json()["info"]["version"] == config.APP_VERSION


class TestSecurityHeaders:
    def test_api_responses_carry_strict_headers(self, client):
        response = client.get("/health/live")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "max-age=" in response.headers["strict-transport-security"]
        assert response.headers["content-security-policy"].startswith("default-src 'none'")

    def test_docs_page_can_load_its_assets_and_be_framed_by_hf(self, client):
        csp = client.get("/docs").headers["content-security-policy"]
        assert "default-src" not in csp
        assert "https://huggingface.co" in csp


class TestCorsConfig:
    def test_unlisted_origin_is_not_allowed(self, client):
        response = client.options(
            "/tasks",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in response.headers

    def test_split_csv_trims_spaces_and_trailing_slashes(self):
        assert config.split_csv(" https://a.app/ ,, https://b.app") == [
            "https://a.app",
            "https://b.app",
        ]


class TestMetricsAccess:
    def test_token_required_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-secret")
        assert client.get("/metrics").status_code == 401
        wrong = client.get("/metrics", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
        assert ok.status_code == 200
        assert "http_requests_total" in ok.text

    def test_hidden_in_production_without_token(self, client, monkeypatch):
        monkeypatch.setattr(config, "METRICS_TOKEN", None)
        monkeypatch.setattr(config, "IS_PRODUCTION", True)
        assert client.get("/metrics").status_code == 404


class TestClientIpForRateLimiting:
    def test_ignores_forwarded_header_unless_trusted(self, monkeypatch):
        monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", False)
        request = make_request("10.0.0.5", "203.0.113.9")
        assert main_module.client_ip(request) == "10.0.0.5"

    def test_uses_rightmost_public_hop_when_trusted(self, monkeypatch):
        monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
        # Client spoofs 1.2.3.4 on the left; the edge proxy appended the real 8.8.4.4;
        # an internal hop appended a private address.
        request = make_request("10.0.0.5", "1.2.3.4, 8.8.4.4, 10.1.2.3")
        assert main_module.client_ip(request) == "8.8.4.4"

    def test_falls_back_to_peer_without_public_hop(self, monkeypatch):
        monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
        assert main_module.client_ip(make_request("10.0.0.5", "garbage, 10.9.9.9")) == "10.0.0.5"
        assert main_module.client_ip(make_request("10.0.0.5")) == "10.0.0.5"


class TestJwtSecretPolicy:
    def test_production_refuses_default_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            auth.validate_secret(auth.DEFAULT_SECRET, is_production=True)

    def test_production_refuses_short_secret(self):
        with pytest.raises(RuntimeError, match="shorter"):
            auth.validate_secret("too-short", is_production=True)

    def test_production_accepts_long_secret(self):
        auth.validate_secret("x" * 48, is_production=True)

    def test_development_only_warns(self, caplog):
        auth.validate_secret(auth.DEFAULT_SECRET, is_production=False)
        assert "JWT_SECRET_KEY" in caplog.text

    def test_token_with_non_numeric_subject_is_rejected(self, client):
        token = auth.create_access_token(subject="not-a-number")
        response = client.get("/tasks", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


class TestDatabaseUrlHandling:
    def test_postgres_scheme_is_rewritten(self):
        assert normalize_database_url("postgres://u:p@h/db") == "postgresql://u:p@h/db"
        assert normalize_database_url("sqlite:///./x.db") == "sqlite:///./x.db"

    def test_postgres_gets_pool_health_options(self):
        assert engine_options("postgresql://u:p@h/db") == {
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }
        assert "connect_args" in engine_options("sqlite:///./x.db")

    def test_sqlite_parent_directory_is_created(self, tmp_path):
        target = tmp_path / "nested" / "dir" / "apiwatch.db"
        ensure_sqlite_directory(f"sqlite:///{target}")
        assert target.parent.is_dir()

    def test_memory_and_non_sqlite_urls_are_left_alone(self):
        ensure_sqlite_directory("sqlite://")
        ensure_sqlite_directory("sqlite:///:memory:")
        ensure_sqlite_directory("postgresql://u:p@h/db")


class TestInputLimits:
    def test_whitespace_only_title_rejected(self, auth_client):
        assert auth_client.post("/tasks", json={"title": "   "}).status_code == 422

    def test_title_is_trimmed(self, auth_client):
        response = auth_client.post("/tasks", json={"title": "  Ship it  "})
        assert response.status_code == 201
        assert response.json()["title"] == "Ship it"

    def test_overlong_fields_rejected(self, auth_client):
        assert auth_client.post("/tasks", json={"title": "x" * 201}).status_code == 422
        too_long = {"title": "ok", "description": "x" * 2001}
        assert auth_client.post("/tasks", json=too_long).status_code == 422

    def test_username_rejects_markup_and_spaces(self, client):
        for bad in ["<script>", "has space", "semi;colon"]:
            response = client.post(
                "/auth/register", json={"username": bad, "password": "a-strong-password"}
            )
            assert response.status_code == 422, bad

    def test_password_is_not_trimmed(self, client):
        creds = {"username": "spacey", "password": "  padded-password  "}
        assert client.post("/auth/register", json=creds).status_code == 201
        assert client.post("/auth/login", data=creds).status_code == 200
        trimmed = {"username": "spacey", "password": "padded-password"}
        assert client.post("/auth/login", data=trimmed).status_code == 401

    def test_password_over_bcrypt_limit_rejected(self, client):
        too_long = {"username": "longpw", "password": "é" * 40}  # 40 chars, 80 bytes
        assert client.post("/auth/register", json=too_long).status_code == 422

    def test_login_with_overlong_password_is_plain_401(self, client):
        creds = {"username": "normalpw", "password": "a-strong-password"}
        client.post("/auth/register", json=creds)
        attempt = {"username": "normalpw", "password": "x" * 500}
        assert client.post("/auth/login", data=attempt).status_code == 401

    def test_login_reports_token_lifetime(self, client):
        creds = {"username": "lifetime", "password": "a-strong-password"}
        client.post("/auth/register", json=creds)
        body = client.post("/auth/login", data=creds).json()
        assert body["expires_in"] == auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60


class TestMe:
    def test_me_returns_current_user(self, auth_client):
        response = auth_client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["username"] == "taskowner"

    def test_me_requires_token(self, client):
        assert client.get("/auth/me").status_code == 401
