# garkboard-lite — single-container HF Space (or local) deployment.
# No database, no API server. Reads directly from the HF Dataset.
#
# Required secrets:  (none — defaults to Jake/glokta-public which is public)
# Optional secrets:  HF_TOKEN (for private repos), HF_DATASET_REPO (to override)

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV HF_DATASET_REPO=Jake/glokta-public
ENV GRADIO_SERVER_PORT=7860

EXPOSE 7860

CMD ["python", "app.py"]
