.PHONY: infra services pipeline api frontend seed test

CONDA_ENV = glokta
COMPOSE    = docker compose --env-file docker/.env -f docker/docker-compose.yml
INFRA      = docker compose --env-file docker/.env -f docker/docker-compose.infra.yml

# Start postgres only (local dev)
infra:
	$(INFRA) up -d

# Start full stack (postgres + prefect-server + prefect-worker + api + frontend)
services:
	$(COMPOSE) up -d

# Run pipeline worker locally against infra (infra must be running)
pipeline:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  python -m glokta.pipeline.serve

# Run API locally with hot-reload (infra must be running)
api:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  uvicorn glokta.api.app:app --reload --host 0.0.0.0 --port 8000

# Run Gradio frontend locally (infra + api must be running)
frontend:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  python -m glokta.frontend.gradio_app

# Seed the model catalog (infra must be running)
seed:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  python scripts/seed_models.py

# Run tests
test:
	PYTHONPATH=src conda run -n $(CONDA_ENV) pytest
