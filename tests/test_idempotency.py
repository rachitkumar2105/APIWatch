class TestIdempotency:
    def test_repeated_post_with_same_key_returns_same_task_not_a_duplicate(self, auth_client):
        payload = {"title": "Idempotent task", "description": "test", "status": "pending"}
        headers = {"Idempotency-Key": "client-generated-key-123"}

        first = auth_client.post("/tasks", json=payload, headers=headers)
        second = auth_client.post("/tasks", json=payload, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        listing = auth_client.get("/tasks").json()
        assert listing["total"] == 1  # not 2 — the retry did not create a duplicate

    def test_different_idempotency_keys_create_separate_tasks(self, auth_client):
        payload = {"title": "Task", "description": "test", "status": "pending"}
        first = auth_client.post("/tasks", json=payload, headers={"Idempotency-Key": "key-a"})
        second = auth_client.post("/tasks", json=payload, headers={"Idempotency-Key": "key-b"})

        assert first.json()["id"] != second.json()["id"]
        listing = auth_client.get("/tasks").json()
        assert listing["total"] == 2

    def test_post_without_idempotency_key_behaves_as_before(self, auth_client):
        payload = {"title": "No key task", "description": "test", "status": "pending"}
        first = auth_client.post("/tasks", json=payload)
        second = auth_client.post("/tasks", json=payload)

        assert first.json()["id"] != second.json()["id"]
        listing = auth_client.get("/tasks").json()
        assert listing["total"] == 2

    def test_idempotency_key_is_scoped_per_user(self, auth_client):
        """The same key string used by two different users must not collide."""
        payload = {"title": "Task", "description": "test", "status": "pending"}
        first_client_resp = auth_client.post(
            "/tasks", json=payload, headers={"Idempotency-Key": "shared-key"}
        )

        from fastapi.testclient import TestClient

        from app.main import app

        other = TestClient(app)
        other.post(
            "/auth/register", json={"username": "seconduser", "password": "a-strong-password"}
        )
        login = other.post(
            "/auth/login", data={"username": "seconduser", "password": "a-strong-password"}
        )
        other.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
        second_client_resp = other.post(
            "/tasks", json=payload, headers={"Idempotency-Key": "shared-key"}
        )

        assert first_client_resp.json()["id"] != second_client_resp.json()["id"]
