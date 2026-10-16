import threading
import time

from fastapi.testclient import TestClient

from app import crud
from app.main import app


def test_concurrent_updates_with_same_version_one_succeeds_one_conflicts(race_env, monkeypatch):
    created = (
        TestClient(app).post("/tasks", json={"title": "Original"}, headers=race_env.headers).json()
    )

    real_get_task = crud.get_task

    def slow_get_task(*args, **kwargs):
        result = real_get_task(*args, **kwargs)
        time.sleep(0.2)  # both requests read version 1 before either one writes
        return result

    monkeypatch.setattr(crud, "get_task", slow_get_task)

    barrier = threading.Barrier(2)
    responses = []

    def worker(title):
        client = TestClient(app)
        barrier.wait()
        responses.append(
            client.put(
                f"/tasks/{created['id']}",
                json={"title": title, "status": "pending", "version": created["version"]},
                headers=race_env.headers,
            )
        )

    threads = [threading.Thread(target=worker, args=(f"Update {n}",)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r.status_code for r in responses) == [200, 409]
