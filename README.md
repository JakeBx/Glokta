# Glokta — Open LLM Security Leaderboard

Glokta is an automated security and intelligence benchmarking platform for LLMs. It runs two complementary evaluation pipelines:

1. **Garak probe scanning** — runs [garak](https://github.com/NVIDIA/garak) probes against LLM endpoints and surfaces comparative security results in a leaderboard dashboard.
2. **CTI living benchmark** — continuously evaluates models against a stream of real-world cyber-threat intelligence tasks (CVE classification, CVSS prediction, threat-actor attribution, ATT&CK technique extraction, exploitation forecasting, and CTI synthesis).

Both pipelines are orchestrated via Prefect, persist results in PostgreSQL, and present everything through a Gradio UI and a REST API.

Built as a project to explore what a reproducible, self-hostable LLM security leaderboard looks like in practice. Garak scores are raw pass rates from named probes — no proprietary weighting, no index. CTI scores are computed against dated, provenance-pinned ground truth from public sources (NVD, CISA KEV, MITRE ATT&CK, Galaxy) and designed to be resistant to training-data leakage.

## Quickstart

```bash
cp docker/.env.docker docker/.env
# Edit docker/.env — set OPENROUTER_API_KEY and POSTGRES_PASSWORD
docker compose -f docker/docker-compose.yml up
```

Once running: Gradio UI at `http://localhost:7860`, API docs at `http://localhost:8000/docs`, Prefect Server at `http://localhost:4200`.

**To enable the CTI benchmark**, also set `CTI_ENABLED=true` in your `.env` file. See [`.env.example`](.env.example) for the full CTI configuration reference.

## Features

### Garak Security Scanning

- **End-to-end garak ingest pipeline** — Prefect worker spawns garak as a subprocess, tails the JSONL output in real time, and streams results to PostgreSQL
- **REST API with multi-axis filtering** — filter the leaderboard by probe category, model, and date; full Swagger UI at `/docs`
- **Leaderboard UI** — filterable table with per-model drill-down showing probe-level breakdowns (Gradio, `localhost:7860` after `docker compose up`)
- **Manual run triggering** — `POST /api/runs` with a model UUID; the Prefect pipeline picks it up on the next 2-minute poll
- **Prefect orchestration** — automatic retries, Prefect Server UI at `localhost:4200` after `docker compose up`, SKIP LOCKED for safe concurrent workers
- **Docker Compose full-stack deployment** — one command starts API, Prefect Server, Prefect worker, Gradio frontend, and PostgreSQL
- **13 probe categories** — `encoding`, `dan`, `goodside`, `promptinject`, `malwaregen`, `continuation`, `lmrc`, `leakreplay`, `snowball`, `badchars`, plus `sysprompt_extraction`, `web_injection`, and expanded DAN sub-probes

### CTI Living Benchmark

The CTI benchmark evaluates how well a model can perform cyber-threat intelligence analysis tasks on real-world, recently-published data. It is designed so that answers cannot be memorised from training data:

- **RCM** — CVE description → CWE weakness classification (set F1 over CWE ids)
- **VSP** — CVE description → CVSS v3.1 severity vector prediction (base-score MAD)
- **ATE** — CTI advisory → ATT&CK technique extraction (set F1 over technique ids)
- **TAA** — Intrusion narrative → threat-actor attribution (alias-aware C/P/I scoring)
- **Forecast** — CVE description → will-it-be-exploited probability (Brier score + AUC; ground truth resolved by CISA KEV)
- **SYN** — Raw inputs → concise threat assessment with hedged claims (claim-set recall + faithfulness; currently disabled pending leakage-gate pilot)

Key design properties:
- **Temporal isolation**: items are stamped with their first-availability date; evaluation slices and pre/post-cutoff tags are derived from that anchor so score analysis can separate memorised from generalised knowledge.
- **Rolling holdout window**: the newest slice of items is withheld from public evaluation for a configurable window (default 14 days) to prevent real-time contamination.
- **Prequential scoring**: run-level scores use Gama et al. fading-factor averaging so recent items carry more weight.
- **Bounded and resumable**: each run caps inference calls, commits results incrementally, and resumes cleanly after interruption.

## API Reference

Full interactive documentation at **http://localhost:8000/docs** (after `docker compose up`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/api/models` | List all registered models |
| `GET` | `/api/models/{id}` | Get a single model by UUID |
| `POST` | `/api/runs` | Trigger a new garak scan job |
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
│   ├── import_from_hf.py       # Import HuggingFace dataset → DB (idempotent merge)
│   ├── seed_cti_model.py       # Seed a model for CTI benchmark validation
│   ├── run_cti_smoke.py        # End-to-end CTI smoke run (no Prefect)
│   └── validate_cti.py         # Validate CTI DB outputs after a smoke run
├── src/glokta/
│   ├── config.py               # Pydantic Settings (env vars, including CTI knobs)
│   ├── api/
│   │   ├── app.py              # FastAPI app factory
│   │   ├── deps.py             # Dependency injection (database session)
│   │   ├── routers/            # health, models, runs, leaderboard
│   │   └── schemas/            # Pydantic request/response schemas
│   ├── application/            # Business logic services
│   │   ├── ingest.py           # garak JSONL → DB parsing
│   │   ├── leaderboard.py      # Leaderboard query logic
│   │   ├── scan_service.py     # Core garak scanning orchestration
│   │   └── cti/
│   │       ├── eval_service.py       # CTI run execution, queuing, and resume
│   │       ├── ingest_service.py     # CTI item upsert with temporal invariants
│   │       ├── forecast_service.py   # KEV upsert, forecast seeding, label resolution
│   │       ├── reference_service.py  # ATT&CK technique + threat-actor reference upsert
│   │       ├── scoring_aggregate.py  # Prequential and AUC run-level aggregation
│   │       └── syn_service.py        # SYN item ingest and pilot gate
│   ├── domain/                 # Pure business entities
│   │   ├── risks.py            # Garak risk category definitions
│   │   └── cti/
│   │       ├── tasks.py        # CTI task registry (RCM/VSP/ATE/TAA/Forecast/SYN)
│   │       ├── scoring.py      # Pure scoring functions (no DB/network)
│   │       └── claims.py       # SYN claim-set types and scoring
│   ├── infrastructure/         # External integrations
│   │   ├── db/
│   │   │   ├── session.py      # SQLAlchemy engine, session factory, init_db()
│   │   │   ├── orm.py          # SQLAlchemy ORM models (garak + CTI tables)
│   │   │   └── repos.py        # Repository pattern for common queries
│   │   ├── garak/
│   │   │   └── runner.py       # Subprocess wrapper for garak CLI
│   │   ├── hf/
│   │   │   ├── client.py       # HuggingFace model discovery
│   │   │   └── sync.py         # HF dataset import/export
│   │   ├── llm/
│   │   │   ├── routing.py      # Shared provider routing (OpenRouter/HF, timeouts, thinking suppression)
│   │   │   └── throttle.py     # Token-bucket rate limiter for CTI inference
│   │   ├── openrouter/
│   │   │   └── client.py       # OpenRouter model catalogue + cost estimation
│   │   └── cti/
│   │       ├── prompts.py      # Prompt templates and response parsers for all CTI tasks
│   │       ├── evaluator.py    # Per-item parse + score adapter
│   │       ├── inference.py    # Direct chat-completions client for CTI inference
│   │       ├── judge.py        # LLM judge for SYN faithfulness residue
│   │       ├── claim_extraction.py  # Claim-set extraction for SYN items
│   │       ├── cutoffs.py      # Curated per-model training cutoff ranges
│   │       └── connectors/
│   │           ├── base.py     # NormalisedCtiItem dataclass
│   │           ├── cve.py      # CVE JSON 5.0 normalisation + delta fetch
│   │           ├── kev.py      # CISA KEV fetch
│   │           ├── attack.py   # MITRE ATT&CK bundle fetch + normalisation
│   │           ├── galaxy.py   # MITRE Galaxy cluster fetch + normalisation
│   │           └── report.py   # CISA advisory RSS fetch + ATE/TAA normalisation
│   ├── pipeline/
│   │   ├── flows.py            # Prefect flows for garak scanning
│   │   ├── cti_flows.py        # Prefect flows for all CTI ingest and eval tasks
│   │   └── serve.py            # Standalone serve entrypoint (no Prefect Server)
│   └── frontend/
│       └── gradio_app.py       # Gradio dashboard
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.pipeline     # Prefect worker image (includes garak + prefect)
│   ├── start-pipeline.sh       # Pipeline startup: wait → deploy → worker start
│   ├── docker-compose.yml
│   └── .env.docker
├── prefect.yaml                # Prefect deployment definitions (garak + CTI flows)
├── tests/                      # unit + integration tests
├── environment.yml
├── pyproject.toml
└── .env.example
```
