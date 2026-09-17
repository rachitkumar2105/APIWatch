class TestHealthChecks:
    def test_liveness_returns_ok_without_auth(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readiness_returns_ready_when_db_reachable(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_readiness_returns_503_when_db_unreachable(self, client, monkeypatch):
        from sqlalchemy.orm import Session

        def broken_execute(self, *args, **kwargs):
            raise Exception("simulated database connection failure")

        monkeypatch.setattr(Session, "execute", broken_execute)
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
