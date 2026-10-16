import threading
import time

from fastapi.testclient import TestClient

from app import crud
from app.main import app
from app.models import Task


def test_concurrent_requests_with_same_idempotency_key_create_one_task(race_env, monkeypatch):
    real_lookup = crud.get_idempotent_response

    def slow_lookup(*args, **kwargs):
        result = real_lookup(*args, **kwargs)
        time.sleep(0.2)  # hold the check-then-act window open so both requests pass the check
        return result

    monkeypatch.setattr(crud, "get_idempotent_response", slow_lookup)

    barrier = threading.Barrier(2)
    responses = []

    def worker():
        client = TestClient(app)
        barrier.wait()
        responses.append(
            client.post(
                "/tasks",
                json={"title": "Raced task"},
                headers={**race_env.headers, "Idempotency-Key": "same-key"},
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with race_env.session_factory() as db:
        task_count = db.query(Task).count()

    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert task_count == 1
