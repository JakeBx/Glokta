---
title: Glokta CTI Leaderboard
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Glokta CTI Leaderboard

Standalone Gradio dashboard for the Glokta CTI benchmark. Reads directly from a
HuggingFace dataset — no API server or database required.

---

## Deploy to HF Spaces

### 1. Export the CTI tables

Run the exporter from your self-hosted Glokta instance to push benchmark data
to a HF dataset:

```bash
# from the glokta repo root
PYTHONPATH=src HF_CTI_DATASET_REPO=your-username/glokta-cti \
  conda run -n glokta python scripts/export_cti_to_hf.py
```

Or inside Docker:

```bash
docker compose -f docker/docker-compose.yml exec api \
  python /app/scripts/export_cti_to_hf.py
```

This creates (or updates) seven configs in the dataset repo:
`models`, `cti_items`, `cti_runs`, `cti_results`,
`cti_attack_techniques`, `cti_threat_actors`, `cti_kev`.

### 2. Create the HF Space

Via the HuggingFace web UI:

1. Go to huggingface.co → New Space
2. Set **SDK** to `Gradio`, **SDK version** to `5.x`
3. Name it something like `your-username/glokta-cti`

Or via the CLI:

```bash
pip install huggingface_hub
huggingface-cli repo create glokta-cti --type space --sdk gradio
```

### 3. Set Space secrets

In the Space settings → **Secrets**, add:

| Secret | Value | Required |
|--------|-------|----------|
| `HF_CTI_DATASET_REPO` | `your-username/glokta-cti` (the dataset repo, not the Space) | Yes |
| `HF_TOKEN` | HuggingFace read token | Only if the dataset repo is private |

### 4. Push the Space files

Clone the Space repo and push the `cti_lite/` folder contents (not the whole
glokta repo):

```bash
git clone https://huggingface.co/spaces/your-username/glokta-cti
cp glokta/cti_lite/* glokta-cti/
cd glokta-cti
git add app.py data.py requirements.txt README.md
git commit -m "deploy cti-lite dashboard"
git push
```

HF Spaces will build and launch automatically.

### 5. Refresh data

The dashboard loads data from HF on startup. To refresh:

1. Re-run `export_cti_to_hf.py` to push updated benchmark data.
2. Click **Refresh Data from HF** in the Space UI, or restart the Space
   (Settings → Factory reset).

---

## Local development

```bash
pip install -r cti_lite/requirements.txt

HF_CTI_DATASET_REPO=your-username/glokta-cti \
  python cti_lite/app.py
```

Or with Gradio CLI:

```bash
HF_CTI_DATASET_REPO=your-username/glokta-cti \
  gradio cti_lite/app.py
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_CTI_DATASET_REPO` | _(required)_ | HF dataset repo containing the exported CTI tables |
| `HF_TOKEN` | _(none)_ | HF read token — only needed for private dataset repos |
| `GRADIO_SERVER_PORT` | `7860` | Port for local runs |
