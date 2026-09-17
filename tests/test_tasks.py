from fastapi.testclient import TestClient

from app import crud
from app.main import app


def create_sample_task(client, title="Write tests", status="pending"):
    return client.post(
        "/tasks",
        json={"title": title, "description": "Sample description", "status": status},
    )


class TestCreateTask:
    def test_create_task_returns_201_with_task_body(self, auth_client):
        response = create_sample_task(auth_client)
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Write tests"
        assert body["status"] == "pending"
        assert "id" in body
        assert "created_at" in body
        assert body["version"] == 1

    def test_create_task_missing_title_returns_422(self, auth_client):
        response = auth_client.post("/tasks", json={"description": "no title"})
        assert response.status_code == 422

    def test_create_task_invalid_status_returns_422(self, auth_client):
        response = auth_client.post(
            "/tasks", json={"title": "Bad status", "status": "not_a_status"}
        )
        assert response.status_code == 422

    def test_create_task_without_auth_returns_401(self, client):
        response = create_sample_task(client)
        assert response.status_code == 401


class TestListTasks:
    def test_list_tasks_returns_created_tasks(self, auth_client):
        create_sample_task(auth_client, title="Task 1")
        create_sample_task(auth_client, title="Task 2")
        response = auth_client.get("/tasks")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_list_tasks_filters_by_status(self, auth_client):
        create_sample_task(auth_client, title="Pending task", status="pending")
        create_sample_task(auth_client, title="Done task", status="done")
        response = auth_client.get("/tasks", params={"status": "done"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Done task"

    def test_list_tasks_empty_returns_empty_items(self, auth_client):
        response = auth_client.get("/tasks")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_tasks_default_limit_is_20(self, auth_client):
        for i in range(25):
            create_sample_task(auth_client, title=f"Task {i}")
        response = auth_client.get("/tasks")
        body = response.json()
        assert body["total"] == 25
        assert len(body["items"]) == 20
        assert body["limit"] == 20
        assert body["offset"] == 0

    def test_list_tasks_offset_skips_items(self, auth_client):
        for i in range(25):
            create_sample_task(auth_client, title=f"Task {i}")
        response = auth_client.get("/tasks", params={"limit": 20, "offset": 20})
        body = response.json()
        assert body["total"] == 25
        assert len(body["items"]) == 5
        assert body["offset"] == 20

    def test_list_tasks_limit_above_100_returns_422(self, auth_client):
        response = auth_client.get("/tasks", params={"limit": 101})
        assert response.status_code == 422

    def test_list_tasks_negative_offset_returns_422(self, auth_client):
        response = auth_client.get("/tasks", params={"offset": -1})
        assert response.status_code == 422

    def test_list_tasks_without_auth_returns_401(self, client):
        response = client.get("/tasks")
        assert response.status_code == 401


class TestGetTask:
    def test_get_task_returns_matching_task(self, auth_client):
        created = create_sample_task(auth_client).json()
        response = auth_client.get(f"/tasks/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_get_nonexistent_task_returns_404(self, auth_client):
        response = auth_client.get("/tasks/9999")
        assert response.status_code == 404


class TestUpdateTask:
    def test_update_task_returns_updated_fields_and_bumps_version(self, auth_client):
        created = create_sample_task(auth_client).json()
        response = auth_client.put(
            f"/tasks/{created['id']}",
            json={"title": "Updated title", "status": "in_progress", "version": created["version"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Updated title"
        assert body["status"] == "in_progress"
        assert body["version"] == created["version"] + 1

    def test_update_nonexistent_task_returns_404(self, auth_client):
        response = auth_client.put(
            "/tasks/9999", json={"title": "Nope", "status": "done", "version": 1}
        )
        assert response.status_code == 404

    def test_update_task_missing_title_returns_422(self, auth_client):
        created = create_sample_task(auth_client).json()
        response = auth_client.put(
            f"/tasks/{created['id']}", json={"status": "done", "version": created["version"]}
        )
        assert response.status_code == 422

    def test_update_task_missing_version_returns_422(self, auth_client):
        created = create_sample_task(auth_client).json()
        response = auth_client.put(
            f"/tasks/{created['id']}", json={"title": "No version field", "status": "done"}
        )
        assert response.status_code == 422


class TestDeleteTask:
    def test_delete_task_removes_it(self, auth_client):
        created = create_sample_task(auth_client).json()
        response = auth_client.delete(f"/tasks/{created['id']}")
        assert response.status_code == 204
        follow_up = auth_client.get(f"/tasks/{created['id']}")
        assert follow_up.status_code == 404

    def test_delete_nonexistent_task_returns_404(self, auth_client):
        response = auth_client.delete("/tasks/9999")
        assert response.status_code == 404


class TestTaskOwnershipIsolation:
    """A user must never be able to see, update, or delete another user's tasks."""

    def _second_user_client(self, client):
        client.post(
            "/auth/register", json={"username": "otheruser", "password": "another-strong-pw"}
        )
        login_resp = client.post(
            "/auth/login", data={"username": "otheruser", "password": "another-strong-pw"}
        )
        token = login_resp.json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        return client

    def test_user_cannot_see_another_users_task(self, auth_client, client):
        created = create_sample_task(auth_client).json()
        other_client = self._second_user_client(TestClient(app))
        response = other_client.get(f"/tasks/{created['id']}")
        assert response.status_code == 404

    def test_user_cannot_update_another_users_task(self, auth_client):
        created = create_sample_task(auth_client).json()
        other_client = self._second_user_client(TestClient(app))
        response = other_client.put(
            f"/tasks/{created['id']}",
            json={"title": "Hijacked", "status": "done", "version": created["version"]},
        )
        assert response.status_code == 404

    def test_user_only_sees_own_tasks_in_list(self, auth_client):
        create_sample_task(auth_client, title="Mine")
        other_client = self._second_user_client(TestClient(app))
        create_sample_task(other_client, title="Theirs")
        mine = auth_client.get("/tasks").json()
        assert mine["total"] == 1
        assert mine["items"][0]["title"] == "Mine"


class TestUnhandledExceptions:
    def test_unhandled_exception_returns_generic_500(self, monkeypatch, auth_client):
        def boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure with sensitive details")

        monkeypatch.setattr(crud, "get_task", boom)
        no_raise_client = TestClient(app, raise_server_exceptions=False)
        no_raise_client.headers.update(dict(auth_client.headers))
        response = no_raise_client.get("/tasks/1")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "sensitive details" not in response.text


class TestMetrics:
    def test_metrics_endpoint_exposed(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "http_requests_total" in response.text


class TestRateLimiting:
    def test_exceeding_write_rate_limit_returns_429(self, auth_client):
        for _ in range(30):
            response = create_sample_task(auth_client)
            assert response.status_code == 201
        blocked = create_sample_task(auth_client)
        assert blocked.status_code == 429


class TestCors:
    def test_preflight_allows_configured_origin(self, client):
        response = client.options(
            "/tasks",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
