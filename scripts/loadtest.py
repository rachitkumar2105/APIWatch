"""Hits GET /tasks N times over a persistent connection and prints latency percentiles.

Usage: start the API (uvicorn app.main:app), then run:
    python scripts/loadtest.py [host] [port] [n]
"""

import http.client
import statistics
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
N = int(sys.argv[3]) if len(sys.argv) > 3 else 200
PATH = "/tasks"

conn = http.client.HTTPConnection(HOST, PORT, timeout=5)

latencies_ms = []
errors = 0
for _ in range(N):
    start = time.perf_counter()
    try:
        conn.request("GET", PATH)
        resp = conn.getresponse()
        resp.read()
        if resp.status != 200:
            errors += 1
    except Exception:
        errors += 1
        conn.close()
        conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    latencies_ms.append((time.perf_counter() - start) * 1000)

conn.close()
latencies_ms.sort()


def pct(p):
    idx = min(int(len(latencies_ms) * p), len(latencies_ms) - 1)
    return latencies_ms[idx]


print(f"target: http://{HOST}:{PORT}{PATH} (persistent connection)")
print(f"requests: {N}, errors: {errors}")
print(f"p50: {pct(0.50):.2f} ms")
print(f"p95: {pct(0.95):.2f} ms")
print(f"p99: {pct(0.99):.2f} ms")
print(f"mean: {statistics.mean(latencies_ms):.2f} ms")
print(f"max: {max(latencies_ms):.2f} ms")
print(f"min: {min(latencies_ms):.2f} ms")
