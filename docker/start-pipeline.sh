#!/bin/bash
# Pipeline worker startup: wait for Prefect Server, deploy flows, start worker.
set -e

PREFECT_API="${PREFECT_API_URL:-http://prefect-server:4200/api}"

echo "Waiting for Prefect Server at ${PREFECT_API}..."
until curl -sf "${PREFECT_API}/health" > /dev/null 2>&1; do
  sleep 3
done
echo "Prefect Server is up."

# Create work pool (idempotent — ignores error if it already exists)
prefect work-pool create --type process garakboard-process-pool 2>/dev/null || true

# Deploy both flows from prefect.yaml
cd /app
prefect deploy --all --prefect-file /app/prefect.yaml

echo "Flows deployed. Starting worker..."
exec prefect worker start --pool garakboard-process-pool
