# GarakBoard — Open LLM Security Leaderboard

GarakBoard is an automated vulnerability scanning platform that runs [garak](https://github.com/NVIDIA/garak) probes against LLM endpoints and surfaces comparative security results in a leaderboard dashboard. It coordinates async scan jobs via Celery + Redis, persists results in PostgreSQL, and presents everything through a Gradio UI and a REST API.

Built as a project to explore what a reproducible, self-hostable LLM security leaderboard looks like in practice. Scores are raw pass rates from named garak probes — no proprietary weighting, no index. Any result can be reproduced by running the same garak command against the same model.

## Features

- **End-to-end garak ingest pipeline** — worker spawns garak as a subprocess, tails the JSONL output in real time, and streams results to PostgreSQL
- **REST API with multi-axis filtering** — filter the leaderboard by probe category, model, and date; full Swagger UI at `/docs`
- **Leaderboard UI** — filterable table with per-model drill-down showing probe-level breakdowns (Gradio, `localhost:7860`)
- **Manual run triggering** — `POST /api/runs` with a model UUID and optional probe category list; status polling included
- **Async job queue** — Celery + Redis with per-model token-bucket rate limiting and automatic 429 retry with exponential back-off
- **Zero direct cost** — targets OpenRouter free-tier models exclusively; no spend required to run the full probe suite
- **Docker Compose full-stack deployment** — one command starts API, worker, Celery Beat, Gradio frontend, PostgreSQL, and Redis
- **10 probe categories** — `encoding`, `dan`, `goodside`, `promptinject`, `malwaregen`, `continuation`, `lmrc`, `leakreplay`, `snowball`, `badchars`

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose V2) — required to run backing services (PostgreSQL, Redis) and optionally the full stack
- **Conda** (Miniconda or Anaconda) — used to manage the Python development environment
- **OpenRouter API key** — you need an account at [openrouter.ai](https://openrouter.ai) with at least **$10 in credits** to run scan jobs against free-tier models. Set the key as `OPENROUTER_API_KEY` in your `.env` file (see below)

## Quick Start — Local Development (Conda)

### 1. Clone and activate the environment

```bash
git clone https://github.com/JakeBx/garak-board.git
cd garak-board
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

This inserts 5 OpenRouter free-tier models into your local database. The script is idempotent — running it again skips models that already exist.

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

### Running services

| Service | URL |
|---------|-----|
| Gradio Dashboard | http://localhost:7860 |
| FastAPI | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

## Running the Test Suite

```bash
# All tests
PYTHONPATH=src conda run -n garakboard pytest tests/ -v

# Unit tests only
PYTHONPATH=src conda run -n garakboard pytest tests/unit/ -v

# Integration tests only
PYTHONPATH=src conda run -n garakboard pytest tests/integration/ -v

# With coverage
PYTHONPATH=src conda run -n garakboard pytest tests/ --cov=garakboard --cov-report=term-missing
```

## Running the Full Stack (Docker Compose)

### 1. Configure environment

```bash
cp docker/.env.docker docker/.env
# Edit docker/.env and set OPENROUTER_API_KEY=<your key>
```

### 2. Build and start all services

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build
```

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
| Flower (Celery) | http://localhost:5555 |

### 5. Tear down

```bash
docker compose -f docker/docker-compose.yml down
# Add -v to also remove volumes (deletes all database data)
```

## Running Your First Scan

### Step 1 — List registered models and copy a model UUID

```bash
curl http://localhost:8000/api/models | python -m json.tool
```

### Step 2 — Submit a scan job

Replace `MODEL_UUID` with the UUID from Step 1:

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"model_id": "MODEL_UUID", "probe_categories": ["encoding"]}'
```

The endpoint returns immediately with a `run_id`. Encoding scans typically take 5–15 minutes.

### Step 3 — Poll run status

Replace `RUN_ID` with the UUID returned in Step 2:

```bash
curl http://localhost:8000/api/runs/RUN_ID
```

Status progression: `pending` → `running` → `completed` (or `failed`).

### Step 4 — View the leaderboard

```bash
curl "http://localhost:8000/api/leaderboard?probe_category=encoding"
```

Or open **http://localhost:7860** for the Gradio dashboard with interactive tables and per-model probe breakdowns.

### Probe categories

| Category | Description |
|----------|-------------|
| `encoding` | Encoding-based token manipulation and injection |
| `promptinject` | Direct prompt injection attacks |
| `dan` | "Do Anything Now" jailbreak variants |
| `goodside` | Gandalf-style prompt override attacks |
| `malwaregen` | Malware and exploit code generation |
| `leakreplay` | Training data extraction and replay |
| `lmrc` | LM Risk Cards — diverse harm categories |
| `continuation` | Harmful text completion |
| `snowball` | Snowballing hallucination and confabulation |
| `badchars` | Unusual character and token handling |

To scan across multiple categories, pass an array: `"probe_categories": ["encoding", "dan"]`.

## API Reference

Full interactive documentation at **http://localhost:8000/docs**.

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

#### Leaderboard query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `probe_category` | string | all | Filter by probe category |
| `model_id` | UUID | — | Filter to a single model |
| `page` | int | 1 | Page number |
| `page_size` | int | 25 | Results per page (max 100) |

## HuggingFace Dataset Sync

GarakBoard can export leaderboard results to a HuggingFace dataset and restore them into any database instance.

### Setup

Add these to your `.env` file:

```
HF_DATASET_REPO=your-username/open-llm-sec-leaderboard
HF_TOKEN=hf_your_write_token_here
```

Install the optional dataset dependencies:

```bash
pip install "garakboard[dataset]"
# or with conda
conda run -n garakboard pip install "datasets>=2.19" "huggingface_hub>=0.23"
```

### Export to HuggingFace

Exports three tables — `models`, `runs`, `probe_results` — as a multi-split dataset and pushes to the Hub.

```bash
# Conda dev env
PYTHONPATH=src python scripts/export_to_hf.py

# Docker
docker compose -f docker/docker-compose.yml exec api python /app/scripts/export_to_hf.py

# Dry-run (no upload, just print counts)
PYTHONPATH=src python scripts/export_to_hf.py --dry-run
```

### Import from HuggingFace

Performs an **idempotent merge** — existing rows are skipped, missing rows are inserted. Safe to run repeatedly.

```bash
# Conda dev env
PYTHONPATH=src python scripts/import_from_hf.py

# Docker
docker compose -f docker/docker-compose.yml exec api python /app/scripts/import_from_hf.py

# Dry-run (download and report without writing to DB)
PYTHONPATH=src python scripts/import_from_hf.py --dry-run
```

#### Merge keys

| Table | Match Key |
|-------|-----------|
| `models` | `id` (UUID) |
| `runs` | `id` (UUID) |
| `probe_results` | `run_id` + `probe_name` + `detector` |

Import order respects FK dependencies: `models` → `runs` → `probe_results`.

---

## Project Structure

```
open-llm-sec/
├── scripts/
│   ├── seed_models.py          # Idempotent model catalogue seeder
│   ├── export_to_hf.py         # Export DB → HuggingFace dataset
│   └── import_from_hf.py       # Import HuggingFace dataset → DB (idempotent merge)
├── src/garakboard/
│   ├── config.py               # Pydantic Settings (env vars)
│   ├── database.py             # SQLAlchemy engine + SessionLocal, init_db()
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic request/response schemas
│   ├── api/
│   │   ├── app.py              # FastAPI app factory
│   │   └── routers/            # health, models, runs, leaderboard
│   ├── ingest/
│   │   └── jsonl_parser.py     # garak JSONL → DB
│   ├── worker/
│   │   ├── celery_app.py       # Celery app + Beat schedule
│   │   ├── tasks.py            # run_scan + discover_and_schedule_scans
│   │   ├── garak_runner.py     # Subprocess wrapper for garak CLI
│   │   └── rate_limiter.py     # Redis token-bucket rate limiter
│   └── frontend/
│       └── gradio_app.py       # Gradio dashboard
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.worker
│   ├── docker-compose.yml
│   └── .env.docker
├── tests/                      # 151 tests (unit + integration)
├── environment.yml
├── pyproject.toml
└── .env.example
```
