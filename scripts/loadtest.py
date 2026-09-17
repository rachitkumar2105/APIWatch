"""Concurrent load test for GET /tasks against a running APIWatch instance.

Unlike an earlier version of this script (sequential, one request at a time),
this fires many requests CONCURRENTLY across a worker pool — closer to real
traffic, where multiple clients call the API at the same moment rather than
politely taking turns.

Usage: start the API (uvicorn app.main:app), then run:
    python scripts/loadtest.py [host] [port] [total_requests] [concurrency]
"""

import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
N = int(sys.argv[3]) if len(sys.argv) > 3 else 200
CONCURRENCY = int(sys.argv[4]) if len(sys.argv) > 4 else 20
BASE_URL = f"http://{HOST}:{PORT}"


def get_auth_token() -> str:
    username = f"loadtest-user-{int(time.time())}"
    password = "loadtest-password-123"
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        c.post("/auth/register", json={"username": username, "password": password})
        resp = c.post("/auth/login", data={"username": username, "password": password})
        resp.raise_for_status()
        return resp.json()["access_token"]


def one_request(client: httpx.Client) -> tuple[float, int]:
    start = time.perf_counter()
    resp = client.get("/tasks")
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, resp.status_code


def main():
    token = get_auth_token()
    headers = {"Authorization": f"Bearer {token}"}

    latencies_ms = []
    errors = 0

    def worker():
        with httpx.Client(base_url=BASE_URL, headers=headers, timeout=10) as client:
            return one_request(client)

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(worker) for _ in range(N)]
        for fut in as_completed(futures):
            try:
                elapsed_ms, status = fut.result()
                latencies_ms.append(elapsed_ms)
                if status != 200:
                    errors += 1
            except Exception:
                errors += 1
    wall_elapsed_s = time.perf_counter() - wall_start

    latencies_ms.sort()

    def pct(p):
        idx = min(int(len(latencies_ms) * p), len(latencies_ms) - 1)
        return latencies_ms[idx]

    print(f"target: {BASE_URL}/tasks")
    print(f"requests: {N}, concurrency: {CONCURRENCY}, errors: {errors}")
    throughput = N / wall_elapsed_s
    print(f"wall clock: {wall_elapsed_s:.3f}s ({throughput:.1f} req/s effective throughput)")
    print(f"p50: {pct(0.50):.2f} ms")
    print(f"p95: {pct(0.95):.2f} ms")
    print(f"p99: {pct(0.99):.2f} ms")
    print(f"mean: {statistics.mean(latencies_ms):.2f} ms")
    print(f"max: {max(latencies_ms):.2f} ms")
    print(f"min: {min(latencies_ms):.2f} ms")


if __name__ == "__main__":
    main()
