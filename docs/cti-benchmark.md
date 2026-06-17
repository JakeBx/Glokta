# CTI Living Benchmark

Glokta's CTI benchmark extends the model-scanning stack from *adversarial robustness* (garak
probes) into *Cyber Threat Intelligence performance*. It turns CTIBench — a static 2024 snapshot
of LLM threat-intel tasks — into a **continuously-regenerating, contamination-resistant**
benchmark built from live authoritative feeds (cvelistV5, CISA KEV, MITRE ATT&CK, MISP galaxy,
CISA advisories).

It runs as a structural parallel to the garak path and shares only the `models` table:

```
garak path:   Run  → ProbeResult(pass/fail)  → risk leaderboard
CTI path:     CtiRun → CtiResult(score, breakdown) → CTI leaderboard
```

The CTI code lives under a `cti` subpackage in each layer (`domain/cti`, `infrastructure/cti`,
`application/cti`, `pipeline/cti_flows.py`) so it is cleanly separable from the scanner.

---

## 1. The six test cases at a glance

| Task | Input → Label | Scoring | Cadence | Leak-resistance | Enabled |
|------|---------------|---------|---------|-----------------|---------|
| **CTI-RCM** | CVE description → CWE id(s) | set F1 | hourly | moderate | ✅ |
| **CTI-VSP** | CVE description → CVSS v3.1 vector | `1 − MAD/7.7` + severity + component distance | hourly | moderate | ✅ |
| **CTI-ATE** | advisory excerpt → ATT&CK technique ids | set F1 (revoked→current) | daily | moderate | ✅ |
| **CTI-TAA** | intrusion narrative → threat actor | synonym graph C/P/I (1.0/0.5/0.0) | daily | low | ✅ |
| **Forecast** | CVE at publication → will-be-exploited? | Brier (+ run-level AUC) | daily | **high (leak-proof)** | ✅ |
| **CTI-SYN** | reconstructed inputs → threat assessment | claim-set recall / faithfulness / calibration | daily | moderate | ✅ |

The registry that drives this table is [`CTI_TASKS`](../src/glokta/domain/cti/tasks.py).
`SYN` is enabled: its input-reconstruction leakage gate is enforced at ingest via the hybrid
masking policy (`ingest_syn_items(mask=True)` masks conclusion labels + drops unmaskable residue),
so the trigger queues it like any other task.

---

## 2. Shared substrate

### Data model

Six new tables ([orm.py](../src/glokta/infrastructure/db/orm.py)), created/migrated by the
existing schema-driven `init_db`/`migrate_db`:

| Table | Holds |
|-------|-------|
| `cti_items` | one `(task, external_id, input, label)` tuple — the dated, provenance-pinned benchmark item |
| `cti_runs` | one model × task evaluation (status, cutoff snapshot, prequential score, AUC) |
| `cti_results` | one model output + score for one item (score, breakdown, `correct`, `pre_cutoff`, `prompt_hash`) |
| `cti_attack_techniques` | ATT&CK reference (id, name, tactic, `revoked_by`) — backs ATE |
| `cti_threat_actors` | actor reference (canonical, aliases, related groups) — backs TAA/SYN |
| `cti_kev` | CISA KEV entries — the Forecast resolver |

The `models` table gains `cutoff_start` / `cutoff_end` (a *range*, since training cutoffs are
fuzzy).

### Inference & prompting

- **Routing** ([routing.py](../src/glokta/infrastructure/llm/routing.py)) — single source of truth
  for provider URI / key / timeout / thinking-suppression, shared with the garak config builder.
  `huggingface/…` → HF router; everything else → OpenRouter.
- **Inference** ([inference.py](../src/glokta/infrastructure/cti/inference.py)) — `complete()`,
  one prompt → one completion, with 429/5xx retry. Paced by a `RateLimiter`
  ([throttle.py](../src/glokta/infrastructure/llm/throttle.py)) at `cti_rpm_limit`. The SYN
  faithfulness **judge always routes via OpenRouter** (`complete_via_openrouter`, i.e.
  `resolve_route(..., force_provider="openrouter")`), independent of the model-under-test's
  provider, so `CTI_JUDGE_MODEL` must be a valid OpenRouter slug (default
  `anthropic/claude-opus-4.8`).
- **Prompts** ([prompts.py](../src/glokta/infrastructure/cti/prompts.py)) — per-task templates +
  answer extractors (ported from athenabench: strip "Answer:" → scan bottom-to-top → per-task
  regex). Model input is capped at 8 K chars; each result stores a `prompt_hash` (SHA256) for
  reproducibility.

### Temporal mechanics (the contamination-control layer)

Enforced centrally in [`ingest_service.upsert_item`](../src/glokta/application/cti/ingest_service.py):

- **Anchor**: `first_available_date = max(input_date, label_date)` — enrichment lags publication.
- **Per-model cutoff**: curated ranges in [cutoffs.py](../src/glokta/infrastructure/cti/cutoffs.py);
  each result is tagged `pre_cutoff` by `first_available_date ≤ model.cutoff_end`. Unknown models
  stay null → treated as post-cutoff (conservative).
- **Prequential scoring** (primary metric): fading-factor accuracy over a recency window
  (Gama et al.), stored on `cti_run.prequential_score`. The pre/post-cutoff gap is *secondary* —
  a drift signal, never contamination proof.
- **Mutable labels**: when a label changes, the prior row is `superseded` and a new active row is
  inserted with a pinned `source_revision`. Cross-authority disagreement (CNA vs ADP vs NVD) is
  kept as an `authority_agreement` difficulty stratum, not discarded.
- **Withhold-newest-slice**: items inside `cti_withhold_window_days` are flagged `withhold` and
  excluded from the public slice; they graduate automatically once the window elapses (refreshed
  on unchanged re-ingest).

### Evaluation loop

[`execute_cti_run`](../src/glokta/application/cti/eval_service.py) is the structural parallel to
`scan_service.execute_scan`:

1. Snapshot the model's cutoff range onto the run.
2. Slice active, non-withheld items for the task — **excluding already-scored items before the
   per-run cap**, so a capped/interrupted run resumes onto the next page.
3. Per item: build prompt → `complete()` → `evaluate_item()` → persist `CtiResult` (committing
   every `cti_eval_commit_every` for resumability).
4. Compute the prequential aggregate (and, for Forecast, run-level AUC).

`process_pending_cti_run` drains the queue (SKIP LOCKED); `queue_cti_runs` enqueues one run per
active model × enabled task that is stale.

---

## 3. Test cases — dataflow · tasking · scoring

### CTI-RCM — CVE → CWE

- **Dataflow.** [`cve` connector](../src/glokta/infrastructure/cti/connectors/cve.py):
  `fetch_recent_cve_records` reads cvelistV5 `delta.json` (the `new`/`updated` arrays, fetching
  only recently-changed records — no full clone) → `normalise_cve_record` extracts the English
  CVE description and CWE id(s) with precedence **CNA → CISA-ADP → NVD** (NVD folded in as a
  cross-check), producing an RCM item. Ingested via `ingest_service`.
- **Tasking.** RCM template asks for the CWE id(s); `complete()` runs the model; `extract_cwes`
  pulls `CWE-\d+` from the model's final answer line.
- **Scoring.** [`score_rcm`](../src/glokta/domain/cti/scoring.py) = set F1 over normalised CWE ids;
  `correct` = exact set match. Breakdown carries precision/recall/tp/fp/fn.

### CTI-VSP — CVE → CVSS vector

- **Dataflow.** Same `cve` connector; the VSP item's label is `{vector, base_score}` (CVSS v3.1),
  same provenance chain.
- **Tasking.** VSP template asks for a `CVSS:3.1/…` vector; `extract_cvss_vector` pulls it (trailing
  punctuation stripped).
- **Scoring.** [`score_vsp`](../src/glokta/domain/cti/scoring.py) parses with the `cvss` library and
  scores `accuracy = max(0, 1 − MAD/7.7)` over base scores (athenabench convention), with breakdown
  adding **severity-band agreement** and **component distance** (fraction of differing base metrics).
  `correct` = severity-band match.

### CTI-ATE — report → ATT&CK techniques

- **Dataflow.** [`report` connector](../src/glokta/infrastructure/cti/connectors/report.py):
  `fetch_advisory_feed` (CISA RSS) → `parse_advisory` (technique/CVE regex + section split) →
  `normalise_report` produces an ATE item labelled with the advisory's technique set. The
  [`attack` connector](../src/glokta/infrastructure/cti/connectors/attack.py) loads
  `cti_attack_techniques` (with `revoked_by`) as the validation/normalisation reference.
- **Tasking.** ATE template asks for technique ids; `extract_techniques` pulls top-level `T\d{4}`
  (sub-technique suffix dropped).
- **Scoring.** [`score_ate`](../src/glokta/domain/cti/scoring.py) = set F1, with both prediction and
  label normalised revoked→current via the technique index. `correct` = exact match.

### CTI-TAA — narrative → threat actor

- **Dataflow.** Same `report` connector; the TAA item's actor label is resolved by `detect_actor`
  against the galaxy alias index. The [`galaxy` connector](../src/glokta/infrastructure/cti/connectors/galaxy.py)
  loads `cti_threat_actors` (aliases + related groups) as the synonym graph.
- **Tasking.** TAA template asks for the actor name; `extract_actor` takes the model's final answer
  line (quote-stripped).
- **Scoring.** [`score_taa`](../src/glokta/domain/cti/scoring.py) — synonym-aware **C/P/I** credit
  (athenabench): **1.0** if prediction resolves to the same canonical actor (alias match), **0.5**
  if it's a *related* group (plausible), **0.0** otherwise. `correct` = C.

### Forecast — CVE → will-be-exploited?

- **Dataflow.** [`forecast_service`](../src/glokta/application/cti/forecast_service.py):
  `seed_forecast_items` creates items from CVE records at publication with a provisional
  `exploited=False` label (anchor = publication date). The
  [`kev` connector](../src/glokta/infrastructure/cti/connectors/kev.py) loads `cti_kev`;
  `resolve_forecast_labels` flips matching items to `exploited=True`, sets `label_date = KEV
  dateAdded`, **and advances the anchor to `max(publication, KEV date)`** — so the exploited label
  is never treated as available before it existed. This is what makes the task structurally
  **leak-proof**: the answer does not exist at submission time.
- **Tasking.** Forecast template asks for a probability in [0,1]; `extract_probability` parses a
  decimal or percentage.
- **Scoring.** [`score_forecast`](../src/glokta/domain/cti/scoring.py) = per-item Brier; stored as
  `1 − Brier` (higher-is-better, consistent with the other tasks), with raw Brier in the breakdown.
  `correct` = `(p ≥ 0.5) == exploited`. Run-level **ROC AUC** (`auc_for_run`, no sklearn) is stored
  on `cti_run.config`; AUC is `None` when only one outcome class is present.

### CTI-SYN — analysis & synthesis

The novel contribution — nobody benchmarks CTI *synthesis*. SYN scores a model's free-text threat
assessment against an analyst product (a CISA advisory) decomposed into a checkable claim set.

- **Dataflow.** From the same advisories
  ([claim_extraction.py](../src/glokta/infrastructure/cti/claim_extraction.py)):
  - `reconstruct_inputs` keeps observation sections (Technical Details / IOC / Overview) and
    **drops conclusion sections** (Summary / Attribution / ATT&CK mapping / Mitigations) — so the
    model scores on *analysis*, not summarisation. The result is the item's `input_text`.
  - `build_claim_set` (**hybrid**): deterministic claims for actor / CVE / technique / IOC, plus a
    pinned LLM (temp 0, cached by advisory hash) for the prose residue — sectors, mitigations, and
    hedge levels. The serialised claim set is the item's `label`.
  - `ingest_syn_items` ([syn_service.py](../src/glokta/application/cti/syn_service.py)).
- **Tasking.** SYN template asks for a structured assessment; `claim_set_from_text` extracts the
  model's claims deterministically (actor via alias graph, CVEs, techniques, IOCs).
- **Scoring.** [`score_syn`](../src/glokta/domain/cti/claims.py) returns three metrics:
  - **recall** — fraction of label claims the model surfaced (objective backbone),
  - **faithfulness** — fraction of model claims grounded in the *inputs* vs hallucinated
    (judge-assisted, [judge.py](../src/glokta/infrastructure/cti/judge.py); the CTI-critical metric,
    weighted highest),
  - **calibration** — hedge alignment vs the advisory's hedging (objective over matched claims).

  The primary score is a weighted blend (faithfulness double-weighted). The input-reconstruction
  leakage gate is enforced at ingest (`ingest_syn_items(mask=True)`); `run_syn_pilot` remains a
  manual spot-check for inspecting reconstructed inputs vs the claim set (e.g. for a new source).

---

## 4. Configuration

All settings live in [config.py](../src/glokta/config.py) (env-overridable; copy `.env.example`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `CTI_ENABLED` | `false` | Master gate for all CTI ingest/eval flows |
| `HF_TOKEN` / `OPENROUTER_API_KEY` | — | Inference credentials (provider chosen by model prefix) |
| `NVD_API_KEY` | `""` | NVD API 2.0 key (50 req/30s with key) for the CVE cross-check |
| `CTI_INGEST_LOOKBACK_DAYS` | `3` | Only ingest items changed within this window (the "recent slice") |
| `CTI_MAX_ITEMS_PER_RUN` | `100` | Hard cap on inference calls per run |
| `CTI_EVAL_COMMIT_EVERY` | `20` | Commit results every N items (enables resume) |
| `CTI_RUN_TIMEOUT_SECONDS` | `3600` | Wall-clock budget per run |
| `CTI_RPM_LIMIT` | `600` | Inference pacing |
| `CTI_PREQUENTIAL_FADING_FACTOR` | `0.99` | Gama recency weighting |
| `CTI_WITHHOLD_WINDOW_DAYS` | `14` | Newest-slice raw-text holdout window |
| `CTI_JUDGE_MODEL` | `anthropic/claude-opus-4.8` | LLM judge for SYN faithfulness |
| `CTI_DATA_DIR` | `/tmp/glokta-cti` | Clone path for the `git_sync` fallback |

---

## 5. Running

### Flows (scheduled, in-stack)

[`pipeline/cti_flows.py`](../src/glokta/pipeline/cti_flows.py), registered in
[prefect.yaml](../prefect.yaml) and [serve.py](../src/glokta/pipeline/serve.py). All gated by
`CTI_ENABLED`:

| Flow | Cadence | Does |
|------|---------|------|
| `cti-ingest-cve` | hourly | recent CVE slice → RCM/VSP items + Forecast seeding |
| `cti-ingest-kev` | daily | KEV → resolve Forecast labels |
| `cti-ingest-attack` | weekly | refresh ATT&CK technique reference |
| `cti-ingest-galaxy` | weekly | refresh threat-actor reference |
| `cti-ingest-report` | daily | CISA+CCCS+NCSC+DFIR advisories → ATE/TAA items (deduped) |
| `cti-ingest-syn` | daily | same deduped advisories → masked SYN items |
| `cti-trigger` | weekly | apply cutoffs, queue runs per active model × enabled task |
| `cti-scan-pending` | 15 min | drain one pending CTI run |

> The Docker `prefect-worker` does **not** receive `CTI_ENABLED` by default, so the scheduled CTI
> flows are inert in-stack until you add it to the worker service environment in
> `docker/docker-compose.yml`.

### One-shot bootstrap

`cti_bootstrap` runs the whole recent slice in dependency order (reference tables → CVE+Forecast →
KEV resolve → advisories → SYN labels), applies cutoffs, queues the enabled non-SYN tasks, and
drains them inline:

```bash
PYTHONPATH=src conda run -n glokta python -c \
  "from glokta.pipeline.cti_flows import cti_bootstrap; cti_bootstrap()"
```

### Scripts (validation / smoke)

For a controlled minimal run against a single model:

```bash
# 1. seed a model (HF Llama-3.1-8B-Instruct), ensure schema, apply cutoffs
PYTHONPATH=src conda run -n glokta python scripts/seed_cti_model.py

# 2. minimal capped live ingest + eval for every task (writes to DB)
PYTHONPATH=src conda run -n glokta python scripts/run_cti_smoke.py

# 3. validate DB outputs per benchmark (exits non-zero on failure)
PYTHONPATH=src conda run -n glokta python scripts/validate_cti.py
```

`run_cti_smoke.py` is bounded (`CTI_SMOKE_MAX_ITEMS`, `CTI_SMOKE_CVE_RECORDS`), tolerates
per-connector failures, and uses a clean synthetic advisory for ATE/TAA/SYN so each benchmark
produces a scored row even when live advisory parsing isn't production-ready.

---

## 6. Known limitations

- **Advisory parsing uses raw HTML.** The CISA feed is reachable, but pages arrive as HTML; ATE/TAA
  on live advisories need an HTML→text + robust section extractor. Validation currently uses a clean
  synthetic advisory for these tasks.
- **`detect_actor` precision.** Greedy substring matching over 1000+ galaxy aliases can mis-pick the
  TAA label; it needs word-boundary / priority matching.
- **SYN faithfulness depends on the judge endpoint.** The faithfulness check needs a reachable
  `CTI_JUDGE_MODEL` (`anthropic/claude-opus-4.8`); without it, recall + calibration still score but
  faithfulness is `None`. The leakage gate is enforced at ingest via masking + drop-residue.
- **Cutoffs are curated estimates.** The map in `cutoffs.py` should be refined as vendors publish
  training-cutoff details.
