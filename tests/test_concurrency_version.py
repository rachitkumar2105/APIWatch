class TestOptimisticLocking:
    """Two callers both read a task, then both try to update it. Without
    version checking, the second write would silently clobber the first
    (last-write-wins). With it, the second write is rejected with 409."""

    def _create_task(self, auth_client):
        return auth_client.post(
            "/tasks", json={"title": "Shared task", "description": "test", "status": "pending"}
        ).json()

    def test_update_with_current_version_succeeds(self, auth_client):
        task = self._create_task(auth_client)
        response = auth_client.put(
            f"/tasks/{task['id']}",
            json={"title": "Updated", "status": "in_progress", "version": task["version"]},
        )
        assert response.status_code == 200
        assert response.json()["version"] == task["version"] + 1

    def test_second_concurrent_update_with_stale_version_returns_409(self, auth_client):
        task = self._create_task(auth_client)

        # Both "readers" fetch the task at version 1.
        reader_a_version = task["version"]
        reader_b_version = task["version"]

        # Reader A updates first — succeeds, task is now at version 2.
        response_a = auth_client.put(
            f"/tasks/{task['id']}",
            json={"title": "A's update", "status": "in_progress", "version": reader_a_version},
        )
        assert response_a.status_code == 200

        # Reader B updates next, still using the STALE version 1 it originally read.
        response_b = auth_client.put(
            f"/tasks/{task['id']}",
            json={"title": "B's update (stale)", "status": "done", "version": reader_b_version},
        )
        assert response_b.status_code == 409

        # A's change survived; B's stale write did NOT silently overwrite it.
        current = auth_client.get(f"/tasks/{task['id']}").json()
        assert current["title"] == "A's update"

    def test_conflict_response_includes_actionable_detail(self, auth_client):
        task = self._create_task(auth_client)
        auth_client.put(
            f"/tasks/{task['id']}",
            json={"title": "First update", "status": "in_progress", "version": task["version"]},
        )
        stale_response = auth_client.put(
            f"/tasks/{task['id']}",
            json={"title": "Stale update", "status": "done", "version": task["version"]},
        )
        assert stale_response.status_code == 409
        assert "version" in stale_response.json()["detail"].lower()
