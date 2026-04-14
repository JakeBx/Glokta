.PHONY: infra services api frontend seed test

CONDA_ENV = open-llm-sec
COMPOSE = docker compose -f docker/docker-compose.yml
INFRA = docker compose -f docker/docker-compose.infra.yml

# Start infrastructure only (postgres + redis)
infra:
	$(INFRA) up -d

# Start infrastructure + worker + beat + flower
services:
	$(INFRA) up -d
	$(COMPOSE) up -d worker beat flower

# Run API locally with hot-reload (infra must be running)
api:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  uvicorn garakboard.api.app:app --reload --host 0.0.0.0 --port 8000

# Run Gradio frontend locally (infra + api must be running)
frontend:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  python -m garakboard.frontend.gradio_app

# Seed the model catalog (infra must be running)
seed:
	PYTHONPATH=src conda run -n $(CONDA_ENV) \
	  python scripts/seed_models.py

# Run tests
test:
	PYTHONPATH=src conda run -n $(CONDA_ENV) pytest
