# Glokta — Open LLM Security Leaderboard

Glokta is an automated vulnerability scanning platform that runs [garak](https://github.com/NVIDIA/garak) probes against LLM endpoints and surfaces comparative security results in a leaderboard dashboard. It orchestrates scan jobs via Prefect, persists results in PostgreSQL, and presents everything through a Gradio UI and a REST API.

Built as a project to explore what a reproducible, self-hostable LLM security leaderboard looks like in practice. Scores are raw pass rates from named garak probes — no proprietary weighting, no index. Any result can be reproduced by running the same garak command against the same model.

## Quickstart

```bash
cp docker/.env.docker docker/.env
# Edit docker/.env — set OPENROUTER_API_KEY and POSTGRES_PASSWORD
docker compose -f docker/docker-compose.yml up
```

Once running: Gradio UI at `http://localhost:7860`, API docs at `http://localhost:8000/docs`, Prefect Server at `http://localhost:4200`.

## Features

- **End-to-end garak ingest pipeline** — Prefect worker spawns garak as a subprocess, tails the JSONL output in real time, and streams results to PostgreSQL
- **REST API with multi-axis filtering** — filter the leaderboard by probe category, model, and date; full Swagger UI at `/docs`
- **Leaderboard UI** — filterable table with per-model drill-down showing probe-level breakdowns (Gradio, `localhost:7860` after `docker compose up`)
- **Manual run triggering** — `POST /api/runs` with a model UUID; the Prefect pipeline picks it up on the next 2-minute poll
- **Prefect orchestration** — automatic retries, Prefect Server UI at `localhost:4200` after `docker compose up`, SKIP LOCKED for safe concurrent workers
- **Zero direct cost** — targets OpenRouter free-tier models exclusively; no spend required to run the full probe suite
- **Docker Compose full-stack deployment** — one command starts API, Prefect Server, Prefect worker, Gradio frontend, and PostgreSQL
- **10 probe categories** — `encoding`, `dan`, `goodside`, `promptinject`, `malwaregen`, `continuation`, `lmrc`, `leakreplay`, `snowball`, `badchars`

## API Reference

Full interactive documentation at **http://localhost:8000/docs** (after `docker compose up`).

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

Glokta can export leaderboard results to a HuggingFace dataset and restore them into any database instance.

---

## Project Structure

```
glokta/
├── scripts/
│   ├── seed_models.py          # Idempotent model catalogue seeder
│   ├── trigger_top_models.py   # Manually queue top OpenRouter models
│   ├── export_to_hf.py         # Export DB → HuggingFace dataset
│   └── import_from_hf.py       # Import HuggingFace dataset → DB (idempotent merge)
├── src/glokta/
│   ├── config.py               # Pydantic Settings (env vars)
│   ├── api/
│   │   ├── app.py              # FastAPI app factory
│   │   ├── deps.py             # Dependency injection (database session)
│   │   ├── routers/            # health, models, runs, leaderboard
│   │   └── schemas/            # Pydantic request/response schemas
│   ├── application/            # Business logic services
│   │   ├── ingest.py           # garak JSONL → DB parsing
│   │   ├── leaderboard.py      # Leaderboard query logic
│   │   └── scan_service.py     # Core scanning orchestration
│   ├── domain/                 # Pure business entities
│   │   └── risks.py            # Risk category definitions and pass rate calculations
│   ├── infrastructure/         # External integrations
│   │   ├── db/
│   │   │   ├── session.py      # SQLAlchemy engine, session factory, init_db()
│   │   │   ├── orm.py          # SQLAlchemy ORM models
│   │   │   └── repos.py        # Repository pattern for common queries
│   │   ├── garak/
│   │   │   └── runner.py       # Subprocess wrapper for garak CLI
│   │   ├── hf/
│   │   │   ├── client.py       # HuggingFace model discovery
│   │   │   └── sync.py         # HF dataset import/export
│   │   └── openrouter/
│   │       └── client.py       # OpenRouter model catalogue + cost estimation
│   ├── pipeline/
│   │   ├── flows.py            # Prefect flows + thin adapter over application services
│   │   └── serve.py            # Standalone serve entrypoint (no Prefect Server)
│   └── frontend/
│       └── gradio_app.py       # Gradio dashboard
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.pipeline     # Prefect worker image (includes garak + prefect)
│   ├── start-pipeline.sh       # Pipeline startup: wait → deploy → worker start
│   ├── docker-compose.yml
│   └── .env.docker
├── prefect.yaml                # Prefect deployment definitions
├── tests/                      # unit + integration tests
├── environment.yml
├── pyproject.toml
└── .env.example
```
