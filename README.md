# GarakBoard — Open LLM Security Leaderboard

GarakBoard is an automated vulnerability scanning platform that runs [garak](https://github.com/NVIDIA/garak) probes against LLM endpoints and surfaces comparative security results in a leaderboard dashboard. It coordinates async scan jobs via Celery + Redis, persists results in PostgreSQL, and presents everything through a Gradio UI and a REST API.

## Why GarakBoard?

# Why GarakBoard

## The problem with existing LLM security leaderboards

Every major LLM security leaderboard in use today shares the same fundamental flaw: the methodology is proprietary. You receive a score. You cannot verify how it was produced, reproduce it independently, or confirm that the same tests will run next month. For teams making procurement or deployment decisions based on these scores, that is a significant trust problem.

GarakBoard is built on NVIDIA's open-source garak scanner. Every score on the leaderboard is a raw pass rate derived from a named, publicly available probe. Any result can be independently reproduced by running the same garak command against the same model. There is no proprietary formula, no hidden weighting, and no black-box index between the test and the number you see.

---

## How the alternatives fall short

### Enkrypt AI LLM Safety Leaderboard

Enkrypt maps results to OWASP Top 10 for LLMs 2025 and NIST risk categories, which is a credible framework choice. The problem is the test suite itself is closed. The leaderboard is a lead-generation tool for a paid platform, and there is no mechanism to verify what prompts were run, inspect individual probe results, or reproduce a score externally.

### Cisco AI Defense Leaderboard

Cisco tests models in their base configuration and splits scoring 50/50 between single-turn and multi-turn attack resistance. The threat categories are mapped to Cisco's internal AI Security and Safety Framework taxonomy — a framework that is not externally auditable. Results cannot be reproduced outside Cisco's infrastructure. The leaderboard serves primarily as a marketing vehicle for Cisco AI Defense.

### CalypsoAI CASI

CalypsoAI uses autonomous agents to simulate persistent adversarial analysts, which is a more sophisticated attack model than most competitors. The CASI score, however, incorporates undisclosed weighting across severity, technical sophistication, and hardware requirements. No independent researcher can reproduce a CASI score, and detailed results require a sales conversation to access.

### Guardion AI

Guardion tests runtime guardrail mitigations rather than base models — a useful but different question. If you want to know how a guardrail layer performs under attack, Guardion is relevant. If you want to compare the inherent safety properties of base models, it is not the right tool. There is no probe-level granularity and no open methodology.

### JailbreakBench

JailbreakBench is the closest to GarakBoard in spirit: open source, reproducible, and academically rigorous. Its scope is narrow by design — 200 jailbreak behaviours only, with no coverage of prompt injection, toxicity, hallucination, or data leakage. It is not maintained as a living leaderboard and does not update as the model ecosystem evolves.

---

## What GarakBoard does differently

**Auditable by design.** garak is open source and actively maintained by NVIDIA and the community. The probe that produced a score is named, documented, and runnable. Security claims on GarakBoard can be verified by any team with an API key and a terminal.

**Probe-level granularity.** Aggregate scores obscure the detail that matters. A model with strong jailbreak resistance and poor prompt injection resistance should not present the same headline number as a model that performs consistently across both. GarakBoard lets you filter the leaderboard by individual probe category so you can evaluate models against the attack surface relevant to your deployment.

**No proprietary scoring.** Scores are pass rates. The number of prompts that passed divided by the number run. No index, no weighting scheme, no adjustments applied after the fact.

**Coverage that grows automatically.** garak's probe library currently covers 150+ attacks across jailbreaks, prompt injection, toxicity, hallucination, data leakage, and encoding-based attacks. As NVIDIA and the open-source community add probes, GarakBoard's coverage expands without manual test suite maintenance.

**Community contributable.** The probe pipeline, scoring logic, and leaderboard infrastructure are all open source. Anyone can add probe categories, extend the model catalogue, propose scoring methodology changes, or improve the ingest pipeline via pull request. The leaderboard improves as the community improves it.

**Run privately against your own models.** Self-host the full stack to scan internal or unreleased models that never leave your environment. Results share the same schema, probes, and scoring methodology as the public leaderboard, giving you a directly comparable security baseline without exposing proprietary model details to any third-party service.

---

## Competitive summary

| Leaderboard | Open methodology | Probe-level filtering | Independently reproducible | Community contributable | Run against private models |
|---|---|---|---|---|---|
| Enkrypt AI | No | No | No | No | No |
| Cisco AI Defense | No | No | No | No | No |
| CalypsoAI CASI | No | No | No | No | No |
| Guardion AI | No | No | No | No | No |
| JailbreakBench | Yes | No — jailbreaks only | Yes | Yes | No |
| **GarakBoard** | **Yes** | **Yes — 150+ probes** | **Yes** | **Yes** | **Yes** |

## Features

- **End-to-end garak ingest pipeline** — worker spawns garak as a subprocess, tails the JSONL output in real time, and streams results to PostgreSQL
- **REST API with multi-axis filtering** — filter the leaderboard by probe category, model, and date; full Swagger UI at `/docs`
- **Leaderboard UI** — filterable table with per-model drill-down showing probe-level breakdowns (Gradio, `localhost:7860`)
- **Manual run triggering** — `POST /api/runs` with a model UUID and optional probe category list; status polling included
- **Async job queue** — Celery + Redis with per-model token-bucket rate limiting and automatic 429 retry with exponential back-off
- **Zero direct cost** — targets OpenRouter free-tier models (`*:free`) exclusively; no spend required to run the full probe suite
- **Docker Compose full-stack deployment** — six services (API, worker, Celery Beat, Gradio frontend, PostgreSQL, Redis) in one command
- **10+ probe categories** — `encoding`, `dan`, `goodside`, `promptinject`, `malwaregen`, `continuation`, `lmrc`, `leakreplay`, `snowball`, `badchars`

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