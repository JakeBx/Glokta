# GarakBoard — Open LLM Security Leaderboard

GarakBoard is an automated vulnerability scanning platform that runs [garak](https://github.com/NVIDIA/garak) probes against LLM endpoints and surfaces comparative security results in a leaderboard dashboard. It coordinates async scan jobs via Celery + Redis, persists results in PostgreSQL, and presents everything through a Gradio UI and a REST API.

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose V2) — required to run backing services (PostgreSQL, Redis) and optionally the full stack
- **Conda** (Miniconda or Anaconda) — used to manage the Python development environment
- **OpenRouter API key** — you need an account at [openrouter.ai](https://openrouter.ai) with at least **$10 in credits** to run scan jobs against free-tier models. Set the key as `OPENROUTER_API_KEY` in your `.env` file (see below)

## Quick Start — Local Development (Conda)

### 1. Clone and activate the environment

```bash
git clone https://github.com/your-org/open-llm-sec.git
cd open-llm-sec
conda env create -f environment.yml
conda activate garakboard
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY=<your key from openrouter.ai>
```

### 3. Start backing services (PostgreSQL + Redis)

```bash
# PostgreSQL 16
docker run -d \
  --name garakboard-postgres \
  -e POSTGRES_DB=garakboard \
  -e POSTGRES_USER=garakboard \
  -e POSTGRES_PASSWORD=garakboard \
  -p 5432:5432 \
  postgres:16-alpine

# Redis 7
docker run -d \
  --name garakboard-redis \
  -p 6379:6379 \
  redis:7-alpine
```

Both containers must be running before you start the API or worker. You can verify with `docker ps`.

### 4. Seed the model catalogue

```bash
PYTHONPATH=src python scripts/seed_models.py
```

This inserts the 27 OpenRouter free-tier models into your local database. The script is idempotent — running it again skips models that already exist.

### 5. Start the API server

```bash
PYTHONPATH=src uvicorn garakboard.api.app:app --reload
```

The API is available at **http://localhost:8000** with interactive docs at **http://localhost:8000/docs**.

### 6. Start the Celery worker (new terminal)

```bash
conda activate garakboard
PYTHONPATH=src celery -A garakboard.worker.celery_app:celery_app worker --loglevel=info
```

### 7. Start the Gradio dashboard (new terminal)

```bash
conda activate garakboard
PYTHONPATH=src python -m garakboard.frontend.gradio_app
```

Open **http://localhost:7860** to access the dashboard.

### Summary of running services

| Service | URL |
|---------|-----|
| Gradio Dashboard | http://localhost:7860 |
| FastAPI | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| PostgreSQL | localhost:5433 (user: garakboard, pass: set via `POSTGRES_PASSWORD` env) |
| Redis | localhost:6379 |

## Running the Test Suite

All 121 tests run from the repo root using the `conda` tool to ensure the correct environment:

```bash
# All tests (full suite)
PYTHONPATH=src conda run -n garakboard pytest tests/ -v

# Unit tests only
PYTHONPATH=src conda run -n garakboard pytest tests/unit/ -v

# Integration tests only
PYTHONPATH=src conda run -n garakboard pytest tests/integration/ -v
```

To run a specific test file:

```bash
PYTHONPATH=src conda run -n garakboard pytest tests/unit/test_models.py -v
```

To run with coverage:

```bash
PYTHONPATH=src conda run -n garakboard pytest tests/ --cov=garakboard --cov-report=term-missing
```

## Running the Full Stack (Docker Compose)

Docker Compose starts all six services: API, worker, Gradio dashboard, PostgreSQL, Redis, and pgAdmin.

### 1. Configure environment

```bash
cp docker/.env.docker docker/.env
# Edit docker/.env and set OPENROUTER_API_KEY=<your key>
```

### 2. Build and start all services

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

On first run this will build the API and worker images. Subsequent starts are faster.

### 3. Seed the model catalogue

```bash
docker compose -f docker/docker-compose.yml exec api python /app/scripts/seed_models.py
```

### 4. Access services

| Service | URL |
|---------|-----|
| Gradio Dashboard | http://localhost:7860 |
| FastAPI | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| pgAdmin | http://localhost:5050 |

### 5. Tear down

```bash
docker compose -f docker/docker-compose.yml down
```

Add `-v` to also remove named volumes (deletes all database data):

```bash
docker compose -f docker/docker-compose.yml down -v
```

## Running Your First Scan

### Step 1 — List registered models and copy a model UUID

```bash
curl http://localhost:8000/api/models | python -m json.tool
```

Pick any model's `id` (UUID format, e.g. `a1b2c3d4-...`).

### Step 2 — Submit a scan job

Replace `MODEL_UUID` with the UUID from Step 1:

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"model_id": "MODEL_UUID", "probe_categories": ["encoding"]}'
```

This submits an "encoding" probe scan against the chosen model. The endpoint returns immediately with a `run_id`. Typical encoding scans take 5–15 minutes depending on model size.

### Step 3 — Poll run status

Replace `RUN_ID` with the UUID returned in Step 2:

```bash
curl http://localhost:8000/api/runs/RUN_ID
```

Status progression: `pending` → `running` → `completed` (or `failed`). Repeat until status is `completed`.

### Step 4 — View the leaderboard

```bash
curl "http://localhost:8000/api/leaderboard?probe_category=encoding"
```

Or open **http://localhost:7860** in your browser for the full Gradio dashboard with interactive tables and probe breakdowns per model.

### Probe categories available

Use any of these as the `probe_categories` value in the scan request:

| Category | Description |
|----------|-------------|
| `encoding` | Token manipulation and injection |
| `prompt_injection` | Direct prompt injection attacks |
| `xss` | Cross-site scripting via model output |
| `问她` | General safety |
| `misinformation` | Disinformation detection |
| `privacy` | Data leakage probes |
| `hallucination` | Confabulation and fact fabrication |

To scan across multiple categories, pass an array: `"probe_categories": ["encoding", "prompt_injection"]`.

## API Reference

Full interactive documentation is available at **http://localhost:8000/docs** (Swagger UI).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/api/models` | List all registered models |
| `GET` | `/api/models/{id}` | Get a single model by UUID |
| `POST` | `/api/runs` | Trigger a new scan job |
| `GET` | `/api/runs` | List all runs (filter: `?status=completed&model_id=<uuid>`) |
| `GET` | `/api/runs/{id}` | Get run status by UUID |
| `GET` | `/api/leaderboard` | Leaderboard with optional filters |
| `GET` | `/api/leaderboard/{model_id}` | Per-model probe breakdown |

### Query parameters for `/api/leaderboard`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `probe_category` | string | all | Filter by probe category |
| `model_id` | UUID | — | Filter to a single model |
| `page` | int | 1 | Page number |
| `page_size` | int | 25 | Results per page (max 100) |

### Example: leaderboard filtered by category

```bash
curl "http://localhost:8000/api/leaderboard?probe_category=xss&page_size=10"
```

## Project Structure

```
open-llm-sec/
├── scripts/
│   └── seed_models.py          # Idempotent model catalogue seeder
├── src/garakboard/
│   ├── config.py               # Pydantic Settings (env vars)
│   ├── database.py             # SQLAlchemy engine + SessionLocal, init_db()
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── model.py            # Model (registered LLM endpoint)
│   │   ├── run.py              # Run (scan job)
│   │   ├── probe_result.py     # ProbeResult (per-probe scores)
│   │   ├── attempt.py          # Attempt (individual probe attempts)
│   │   └── probe_run_queue.py  # ProbeRunQueue (worker queue)
│   ├── schemas/                # Pydantic request/response schemas
│   ├── api/
│   │   ├── app.py              # FastAPI app factory
│   │   ├── deps.py             # Dependency injection helpers
│   │   └── routers/
│   │       ├── health.py       # GET /api/health
│   │       ├── models.py        # GET /api/models
│   │       ├── runs.py         # POST/GET /api/runs
│   │       └── leaderboard.py  # GET /api/leaderboard
│   ├── ingest/
│   │   └── jsonl_parser.py    # garak JSONL → DB (attempt + probe_result)
│   ├── worker/
│   │   ├── celery_app.py       # Celery app instance
│   │   ├── tasks.py            # run_scan task + publish_run_job
│   │   ├── garak_runner.py     # Subprocess wrapper for garak CLI
│   │   └── rate_limiter.py     # Redis token-bucket rate limiter
│   └── frontend/
│       └── gradio_app.py       # Gradio dashboard UI
├── docker/
│   ├── Dockerfile.api          # API container
│   ├── Dockerfile.worker       # Celery worker container
│   ├── docker-compose.yml      # 6-service stack
│   └── .env.docker             # Env template for Docker
├── tests/                      # 121 passing tests
│   ├── conftest.py             # Pytest fixtures
│   ├── unit/                   # Unit tests
│   └── integration/             # API integration tests
├── plans/
│   ├── scope.md                # Phase 1 scope and goals
│   └── design.md               # Architecture and design decisions
├── environment.yml             # Conda environment spec
├── pyproject.toml              # Project metadata
├── .env.example                # Env var template
└── README.md                   # This file
```

## Phase 2 Notes

The following are intentionally deferred to Phase 2:

- **GKE deployment** — The architecture is designed for Google Kubernetes Engine. All configuration is env-var driven, workers are stateless, and rate-limit coordination is managed through Redis. To migrate to GKE, containerise the API and worker images, apply the Kubernetes manifests, and point `DATABASE_URL` and `REDIS_URL` at managed services (Cloud SQL + Memorystore).

- **Scheduled scan runs (Celery Beat)** — Currently all scans are triggered via `POST /api/runs`. Phase 2 will add Celery Beat periodic tasks to run weekly probe sweeps across the full model catalogue automatically.

- **Agentic model discovery** — The model catalogue is seeded manually via `scripts/seed_models.py`. Phase 2 will introduce an automated discovery service that queries the OpenRouter API periodically, detects newly available free-tier models, and registers them in the database without manual intervention.

- **Enhanced leaderboard analytics** — Probe result aggregation and trend tracking over time will be added in Phase 2 to support longitudinal security benchmarking.