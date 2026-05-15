#!/usr/bin/env python3
"""
Smoke test: run a minimal garak scan via the Docker-deployed Glokta stack.

Equivalent to scripts/smoke_test_garak.py but exercises the full deployment:
  API → Celery worker → garak subprocess → JSONL ingest → DB write.

Prerequisites:
    docker compose -f docker/docker-compose.yml --env-file docker/.env up -d
    # Then seed models:
    docker compose -f docker/docker-compose.yml --env-file docker/.env exec api \
        python -c "from glokta.database import SessionLocal, init_db; ..."

Usage:
    python scripts/smoke_test_docker.py
"""

import json
import sys
import time
import urllib.error
import urllib.request

API_BASE = "http://localhost:8000/api"
MODEL_NAME = "openrouter/google/gemini-2.5-flash-lite"
PROBES = ["goodside.ThreatenJSON"]
POLL_INTERVAL = 5        # seconds between status polls
TIMEOUT_SECONDS = 300    # 5 minutes max


def api_get(path: str) -> dict | list:
    """GET request to the API, returning parsed JSON."""
    req = urllib.request.Request(f"{API_BASE}{path}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def api_post(path: str, body: dict) -> dict:
    """POST request to the API with a JSON body, returning parsed JSON."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def wait_for_api(max_wait: int = 60) -> None:
    """Block until the API health endpoint responds."""
    print(f"Waiting for API at {API_BASE}/health ...")
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            api_get("/health")
            print("  API is healthy.")
            return
        except Exception:
            time.sleep(2)
    print("ERROR: API did not become healthy within timeout.")
    sys.exit(1)


def find_model_id(name: str) -> str:
    """Look up a model by name and return its UUID."""
    models = api_get("/models")
    for m in models:
        if m["name"] == name:
            return m["id"]
    print(f"ERROR: Model '{name}' not found in API.")
    print(f"  Available models: {[m['name'] for m in models]}")
    print("  Seed models first:  docker compose ... exec api python /app/scripts/seed_models.py")
    sys.exit(1)


def main() -> None:
    print("Glokta Docker smoke test")
    print(f"  API   : {API_BASE}")
    print(f"  model : {MODEL_NAME}")
    print(f"  probes: {PROBES}")
    print(f"  timeout: {TIMEOUT_SECONDS}s")
    print()

    # 1. Wait for API
    wait_for_api()

    # 2. Find model
    model_id = find_model_id(MODEL_NAME)
    print(f"  model_id: {model_id}")

    # 3. Create run
    print("\nCreating run via POST /api/runs ...")
    run = api_post("/runs", {
        "model_id": model_id,
        "probe_categories": PROBES,
    })
    run_id = run["id"]
    print(f"  run_id: {run_id}  status: {run['status']}")

    # 4. Poll until complete or failed
    print("\nPolling for completion ...")
    start = time.monotonic()
    last_status = run["status"]

    while True:
        elapsed = time.monotonic() - start
        if elapsed > TIMEOUT_SECONDS:
            print(f"\nFAIL — timed out after {elapsed:.0f}s (status was '{last_status}')")
            sys.exit(1)

        try:
            run = api_get(f"/runs/{run_id}")
        except Exception as exc:
            print(f"  [{elapsed:5.0f}s] poll error: {exc}")
            time.sleep(POLL_INTERVAL)
            continue

        status = run["status"]
        if status != last_status:
            print(f"  [{elapsed:5.0f}s] status: {last_status} → {status}")
            last_status = status
        else:
            print(f"  [{elapsed:5.0f}s] status: {status}")

        if status == "complete":
            print(f"\nPASS — run completed in {elapsed:.1f}s")
            sys.exit(0)
        elif status == "failed":
            print(f"\nFAIL — run failed after {elapsed:.1f}s")
            sys.exit(1)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
