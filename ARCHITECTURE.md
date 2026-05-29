# Glokta Architecture

This document describes the layered architecture introduced in the `refactor/structural-improvements` branch.

## Overview

Glokta follows a **clean architecture** pattern with clear separation of concerns:

```
src/glokta/
├── domain/              # Business entities and pure logic
├── application/         # Use cases and orchestration
├── infrastructure/      # External integrations (DB, garak, HF, OpenRouter)
└── api/                 # Web API layer (FastAPI)
```

## Layer Responsibilities

### Domain Layer (`src/glokta/domain/`)

**Pure business logic with no external dependencies.**

- **`risks.py`** – Risk category definitions and pass rate calculations
  - `RISK_DEFINITIONS` – mapping of probe categories to human-readable labels
  - `ACTIVE_RISKS` – list of enabled risk categories
  - `compute_risk_pass_rates()` – pure function for leaderboard scoring

**Key principle**: Domain objects have no knowledge of databases, HTTP, or external services.

### Application Layer (`src/glokta/application/`)

**Orchestrates business use cases using domain entities and infrastructure.**

- **`scan_service.py`** – Core scanning orchestration
  - `process_pending_run()` – picks pending runs, marks as running, calls scan function
  - `execute_scan()` – runs garak, ingests results, updates run status
  - `discover_and_queue()` – fetches top models from OpenRouter/HF, creates pending runs
  - `queue_coverage_remediation()` – finds models with missing risk coverage, queues targeted runs
  - `reap_stale_runs()` – marks stuck "running" runs as "failed"

- **`leaderboard.py`** – Leaderboard query logic
  - `LeaderboardService` class with methods for paginated leaderboard, model detail, risk leaderboard, trends
  - Pure SQLAlchemy queries, no FastAPI dependencies

- **`ingest.py`** – JSONL parsing and database insertion
  - `ingest_jsonl_file()` – parses garak output, creates `ProbeResult` and `Attempt` records
  - `parse_eval_entry()`, `parse_attempt_entry()` – pure parsing functions

**Key principle**: Application services coordinate domain logic and infrastructure, but remain framework-agnostic (no Prefect/FastAPI dependencies).

### Infrastructure Layer (`src/glokta/infrastructure/`)

**External dependencies and implementation details.**

- **`db/`** – Database access
  - `session.py` – SQLAlchemy engine, session factory, `init_db()`, `migrate_db()`
  - `orm.py` – SQLAlchemy ORM models (`Model`, `Run`, `ProbeResult`, `Attempt`, `ProbeRunQueue`)
  - `repos.py` – Repository pattern for common queries (`ModelRepository`, `RunRepository`, `ProbeResultRepository`)

- **`garak/`** – Garak integration
  - `runner.py` – `build_garak_config()`, `run_garak()`, `compute_remaining_probes()`

- **`hf/`** – HuggingFace integration
  - `client.py` – `fetch_top_hf_models()`
  - `sync.py` – `import_all()` for HF dataset sync

- **`openrouter/`** – OpenRouter integration
  - `client.py` – `fetch_top_models()`, `estimate_scan_cost_usd()`

**Key principle**: Infrastructure components are replaceable implementations of interfaces that the application layer depends on.

### API Layer (`src/glokta/api/`)

**Web API endpoints and request/response schemas.**

- **`app.py`** – FastAPI app factory
- **`deps.py`** – Dependency injection (database session)
- **`routers/`** – FastAPI route handlers
  - `health.py` – health check endpoint
  - `models.py` – model CRUD
  - `runs.py` – run creation, listing, verification
  - `leaderboard.py` – thin adapter over `LeaderboardService`
- **`schemas/`** – Pydantic models for request/response validation
  - `model.py`, `run.py`, `probe_result.py`, `leaderboard.py`

**Key principle**: API layer is a thin adapter that translates HTTP requests to application service calls.

### Pipeline Layer (`src/glokta/pipeline/`)

**Prefect-specific orchestration.**

- **`flows.py`** – Prefect flows and tasks
  - Thin wrapper around application services
  - `execute_garak_scan_task()` – Prefect task with retry logic
  - `scan_pending_runs()`, `discover_and_queue_scans()`, `remediate_coverage_gaps()` – Prefect flows
  - Backward-compat aliases for tests: `_process_pending_runs = process_pending_run`, etc.

- **`serve.py`** – Standalone serve entrypoint (no Prefect Server)

**Key principle**: Pipeline layer adds scheduling, retries, and monitoring on top of application services.

## Dependency Direction

```
API Layer → Application Layer → Domain Layer
      ↓          ↓
Infrastructure Layer
```

- **Domain** knows nothing about other layers
- **Application** depends on Domain and Infrastructure interfaces
- **API** depends on Application services and Infrastructure
- **Infrastructure** implements details for Application

## Key Design Decisions

### 1. Repository Pattern (Partial Adoption)

The infrastructure layer includes repositories (`ModelRepository`, `RunRepository`, `ProbeResultRepository`) but the application layer mixes repository usage with direct `db.query()` calls.

**Rationale**: Gradual migration path. Repositories improve testability for complex queries; simple queries remain as direct SQLAlchemy for readability.

### 2. Backward Compatibility Aliases

`flows.py` contains aliases like `_process_pending_runs = process_pending_run` to maintain test compatibility without updating all test imports.

**Rationale**: Allows incremental migration. These should be removed once all callers are updated.

### 3. Risk Definitions in Domain

Risk categories and scoring logic moved from `risks.py` to `domain/risks.py` as pure functions.

**Rationale**: Risk definitions are core business logic, not infrastructure or API concerns.

### 4. Service Classes vs Functions

- `LeaderboardService` is a class (stateful with database session)
- `scan_service` functions are stateless (receive `Session` as parameter)

**Rationale**: Leaderboard queries benefit from shared query-building methods; scanning functions are procedural.

## Migration Guide

### For Developers

**New imports**:
```python
# Old
from glokta.models import Model, Run
from glokta.schemas import ModelResponse
from glokta.database import SessionLocal
from glokta.worker.garak_runner import build_garak_config

# New
from glokta.infrastructure.db.orm import Model, Run
from glokta.api.schemas import ModelResponse
from glokta.infrastructure.db.session import SessionLocal
from glokta.infrastructure.garak.runner import build_garak_config
```

**Application services**:
```python
from glokta.application.scan_service import execute_scan, process_pending_run
from glokta.application.leaderboard import LeaderboardService
from glokta.application.ingest import ingest_jsonl_file
```

### For Test Writers

**Mock patches**:
```python
# Old
@patch("glokta.pipeline.flows.fetch_top_models")

# New
@patch("glokta.application.scan_service.fetch_top_models")
```

### For Script Maintainers

All utility scripts in `scripts/` have been updated to use new import paths.

## Future Evolution

1. **Complete repository pattern** – Convert all direct queries to repository methods
2. **Remove backward-compat aliases** – Update remaining test imports
3. **Add dependency injection** – Inject repositories into application services
4. **Extract interfaces** – Define protocols for infrastructure components
5. **Add unit of work pattern** – Manage transaction boundaries in application layer

## Benefits Achieved

- **Testability**: Application logic can be tested without Prefect/FastAPI
- **Maintainability**: Clear boundaries between concerns
- **Flexibility**: Infrastructure components can be swapped (e.g., different LLM providers)
- **Onboarding**: New developers understand architecture at a glance