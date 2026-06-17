# CTI Benchmark — Deploy & Run Sheet

End-to-end runbook to deploy the living CTI benchmark and **populate the full test set**
(reference data → advisories → CVEs → labels → scored runs). Two deploy modes; the data-population
steps (§3–§6) are the same in both.

---

## 0. Prerequisites

- `conda` env `glokta` (`conda activate glokta`), Python 3.12+.
- Docker + Docker Compose.
- Secrets: `POSTGRES_PASSWORD`, `HF_TOKEN` (model-under-test inference), `OPENROUTER_API_KEY`
  (routes the **SYN faithfulness judge** `anthropic/claude-opus-4.8` + other providers).

---

## 1. Configure environment

Set these in `docker/.env` (Mode A — copy from `docker/.env.docker`) **or** the repo `.env` (Mode B):

```bash
# --- core ---
DATABASE_URL=postgresql://glokta:CHANGEME@localhost:5432/glokta   # Mode B; Mode A builds it internally
POSTGRES_PASSWORD=CHANGEME
CTI_ENABLED=true                      # REQUIRED — flows are inert when false
HF_TOKEN=hf_xxx                       # model-under-test (HF router)
OPENROUTER_API_KEY=sk-or-xxx          # SYN judge routing (anthropic/claude-opus-4.8)
CTI_JUDGE_MODEL=anthropic/claude-opus-4.8

# --- extraction sizing (set large for the one-shot backfill; revert after, see §7) ---
CTI_INGEST_LOOKBACK_DAYS=3650         # steady-state: 3
CTI_REPORT_SOURCES=cisa,cccs,ncsc,dfir
CTI_REPORT_MAX_PER_SOURCE=200         # steady-state: 20
CTI_WITHHOLD_WINDOW_DAYS=14           # newest items held out of scoring
CTI_MAX_ITEMS_PER_RUN=100             # per-run inference cap (runs resume)
```

> Env var names are the uppercase of the `settings` fields (e.g. `CTI_INGEST_LOOKBACK_DAYS` →
> `cti_ingest_lookback_days`).

---

## 2. Deploy — pick one mode

### Mode A — Full stack (Docker, scheduled) — recommended for production

```bash
cd docker
docker compose up -d postgres prefect-server
docker compose up -d prefect-worker api frontend
```

The worker runs `start-pipeline.sh`: creates the `glokta-process-pool`, runs `prefect deploy --all`
(from `prefect.yaml`), and starts the worker — so **all CTI flows are deployed and scheduled**
(cve hourly; kev/report/syn daily; attack/galaxy/trigger weekly; scan-pending every 15 min).
Prefect UI: `http://localhost:4200`.

### Mode B — Local (conda, no Prefect server) — for backfill / debugging

```bash
docker compose -f docker/docker-compose.infra.yml up -d        # postgres only
conda activate glokta
export DATABASE_URL=postgresql://glokta:$POSTGRES_PASSWORD@localhost:5432/glokta
export CTI_ENABLED=true HF_TOKEN=hf_xxx OPENROUTER_API_KEY=sk-or-xxx
export CTI_JUDGE_MODEL=anthropic/claude-opus-4.8
```

Run flows/scripts directly (all commands below assume `PYTHONPATH=src`).

---

## 3. Initialise schema + seed the model(s)

```bash
PYTHONPATH=src python scripts/seed_cti_model.py
```

`seed_cti_model.py` runs `init_db()` (creates the CTI tables + migrations), seeds
`huggingface/meta-llama/Llama-3.1-8B-Instruct` as an **active** model, and applies the curated
cutoff map. For more models: `scripts/seed_models.py` or the `sync-top-models` flow.

---

## 4. Populate the full test set (extraction)

Order matters: **reference tables → advisories → CVEs → labels**.

### 4a. Reference data (ATT&CK technique index + actor alias graph)

```bash
PYTHONPATH=src python -c "
from glokta.pipeline.cti_flows import cti_ingest_attack, cti_ingest_galaxy
cti_ingest_attack(); cti_ingest_galaxy()"
```

Needed before advisories so ATE technique-normalisation and TAA/SYN actor-aliasing work.

### 4b. Advisory corpus → ATE / TAA / SYN  (multi-source, deduped, masked)

With the large `CTI_INGEST_LOOKBACK_DAYS` / `CTI_REPORT_MAX_PER_SOURCE` from §1:

```bash
# optional: pre-cache pages to disk (resumable) for faster re-runs
PYTHONPATH=src python scripts/download_advisories.py 200 cisa cccs ncsc dfir

PYTHONPATH=src python -c "
from glokta.pipeline.cti_flows import cti_ingest_report, cti_ingest_syn
cti_ingest_report()   # CISA+CCCS+NCSC+DFIR -> ATE/TAA items (deduped)
cti_ingest_syn()      # same deduped pool   -> masked SYN items"
```

Pulls the full available corpus (CISA ~180, DFIR ~95, CCCS/NCSC tails), dedupes joint advisories
across sources, and ingests SYN with the masking + drop-residue gate.

### 4c. CVE slice → RCM / VSP / Forecast, then KEV labels

**Recent slice (default, living benchmark):**

```bash
PYTHONPATH=src python -c "
from glokta.pipeline.cti_flows import cti_ingest_cve, cti_ingest_kev
cti_ingest_cve()    # delta feed -> RCM/VSP items + Forecast seeds
cti_ingest_kev()    # KEV -> resolves Forecast 'exploited' labels"
```

> The cvelistV5 **delta feed is a rolling ~2-hour snapshot**, not history — RCM/VSP/Forecast
> accumulate over time via the hourly flow. For an immediate larger CVE set, clone the repo and
> ingest a **date-bounded, capped** slice (the full repo is ~270k records — always bound it).
> Note `git_sync` returns the **commit SHA** (the revision pin), so walk the **directory** with
> `iter_cve_records`, not the SHA:
>
> ```bash
> CVE_BACKFILL_DAYS=30 CVE_BACKFILL_MAX=500 PYTHONPATH=src python -c "
> import os, glokta.infrastructure.db.orm
> from datetime import date, timedelta
> from glokta.infrastructure.cti.connectors.cve import git_sync, iter_cve_records
> from glokta.application.cti.ingest_service import ingest_cve_records
> from glokta.application.cti.forecast_service import seed_forecast_items
> from glokta.infrastructure.db.session import SessionLocal
> DATA_DIR='/tmp/glokta-cti'
> cutoff=(date.today()-timedelta(days=int(os.environ['CVE_BACKFILL_DAYS']))).isoformat()
> sha=git_sync(DATA_DIR)                                 # returns the commit SHA (revision pin)
> recent=[r for r in iter_cve_records(DATA_DIR)          # walk the PATH, not the SHA
>         if (r.get('cveMetadata',{}).get('datePublished') or '')[:10] >= cutoff]
> recent.sort(key=lambda r: r['cveMetadata']['datePublished'], reverse=True)
> cap=int(os.environ.get('CVE_BACKFILL_MAX','0'))
> if cap: recent=recent[:cap]
> db=SessionLocal()
> print('cve ingest:', ingest_cve_records(db, recent, sha, withhold_window_days=14))
> print('forecast :', seed_forecast_items(db, recent, sha)); db.close()
> print('CVEs ingested:', len(recent))"
> # then resolve Forecast labels against KEV:
> PYTHONPATH=src python -c "from glokta.pipeline.cti_flows import cti_ingest_kev; cti_ingest_kev()"
> ```

---

## 5. Apply cutoffs, queue, and score

**One-shot (recommended for the initial extraction)** — does reference→cve→kev→report→syn, applies
cutoffs, queues runs for **all six tasks** (incl. SYN), and drains them inline:

```bash
PYTHONPATH=src python -c "from glokta.pipeline.cti_flows import cti_bootstrap; cti_bootstrap()"
```

> `cti_bootstrap` re-runs the ingest flows with the current env sizing, so §4 is optional if you
> run bootstrap directly. It is **not** a Prefect deployment — invoke it directly.

**Or the scheduled path** (Mode A does this automatically): `cti-trigger` queues runs per active
model × enabled task; `cti-scan-pending` drains one run per cycle. Manual equivalent:

```bash
PYTHONPATH=src python -c "from glokta.pipeline.cti_flows import cti_trigger; cti_trigger()"
# then drain (repeat until no pending runs remain):
PYTHONPATH=src python -c "from glokta.pipeline.cti_flows import cti_scan_pending; cti_scan_pending()"
```

---

## 6. Validate

```bash
PYTHONPATH=src python scripts/validate_cti.py
```

Checks every task (incl. SYN) reached `complete` with scored results carrying `score`,
`pre/post-cutoff`, `prompt_hash`; that Forecast recorded a run-level AUC; and that model cutoffs
are populated. Exits non-zero on any failure.

Quick DB spot-check:

```sql
SELECT task, COUNT(*) FROM cti_items GROUP BY task;                    -- corpus per task
SELECT task, status, COUNT(*) FROM cti_runs GROUP BY task, status;    -- runs reached complete
SELECT r.task, COUNT(*) FILTER (WHERE res.pre_cutoff) AS pre,
       COUNT(*) FILTER (WHERE NOT res.pre_cutoff) AS post
FROM cti_results res JOIN cti_runs r ON r.id = res.run_id GROUP BY r.task;
```

---

## 7. Return to steady-state

After the backfill, revert the sizing so daily flows fetch only the recent window:

```bash
CTI_INGEST_LOOKBACK_DAYS=3
CTI_REPORT_MAX_PER_SOURCE=20
```

In Mode A the schedules then keep the benchmark living (new advisories/CVEs daily, weekly
reference refresh + trigger). New models: add via `seed_models.py` / `sync-top-models`; `cti-trigger`
queues them automatically.

---

## Gotchas / operational notes

- **`CTI_ENABLED=true`** or every CTI flow returns immediately.
- **SYN judge**: faithfulness needs `OPENROUTER_API_KEY` routing to `CTI_JUDGE_MODEL`
  (`anthropic/claude-opus-4.8`). Without it, recall + calibration still score but **faithfulness is `None`**.
- **Withhold window**: items whose `first_available_date` is within `CTI_WITHHOLD_WINDOW_DAYS` (14)
  are held out of scoring, so `scored_n < ingested_n` for the newest slice. Historical backfill
  (old advisory dates) scores immediately and is tagged `pre_cutoff` per model — that's the
  contamination signal, by design.
- **Per-run cap** `CTI_MAX_ITEMS_PER_RUN` (100) bounds inference cost; a run resumes across
  `cti-scan-pending` cycles until the slice is exhausted.
- **Adding a source later**: re-validate the SYN leakage gate with
  `docs/walkthrough/07_syn_pilot_recent_advisories.ipynb` before relying on it.
