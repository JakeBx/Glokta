# CTI Benchmark Walkthroughs

Runnable Jupyter notebooks that walk through each CTI test end to end — **dataflow → tasking →
scoring** — using a handful of in-memory data examples and real model calls. Each notebook uses
the project's actual functions (`normalise_*`, `build_prompt`, `evaluate_item`, `score_*`), so
what you see is exactly what the pipeline does.

For the architecture and per-test reference, see [../CTI Benchmark.md](../CTI%20Benchmark.md).

## Notebooks

| Notebook | Test | What it shows |
|----------|------|---------------|
| [01_rcm_cve_to_cwe.ipynb](01_rcm_cve_to_cwe.ipynb) | CTI-RCM | CVE description → CWE; set-F1 scoring + CNA/ADP/NVD provenance |
| [02_vsp_cve_to_cvss.ipynb](02_vsp_cve_to_cvss.ipynb) | CTI-VSP | CVE → CVSS vector; `1 − MAD/7.7`, severity band, component distance |
| [03_ate_report_to_techniques.ipynb](03_ate_report_to_techniques.ipynb) | CTI-ATE | report → ATT&CK techniques; set F1 with revoked→current normalisation |
| [04_taa_narrative_to_actor.ipynb](04_taa_narrative_to_actor.ipynb) | CTI-TAA | narrative → actor; synonym-graph **C/P/I** credit (1.0 / 0.5 / 0.0) |
| [05_forecast_cve_exploitation.ipynb](05_forecast_cve_exploitation.ipynb) | Forecast | CVE → exploited?; Brier + run-level AUC; the leak-proof design |
| [06_syn_analysis_synthesis.ipynb](06_syn_analysis_synthesis.ipynb) | CTI-SYN | inputs reconstruction → claim set; recall / faithfulness / calibration |
| [07_syn_pilot_recent_advisories.ipynb](07_syn_pilot_recent_advisories.ipynb) | CTI-SYN pilot | runs SYN against **recent CISA advisories**, measures conclusion **leakage**, and reports per-advisory + aggregate scores — the input-reconstruction gate in notebook form |
| [08_source_review_national_certs.ipynb](08_source_review_national_certs.ipynb) | Source review | reviews **ACSC / NCSC / CCCS** advisory feeds as additional sources; scores each against the ingest path (index, structure, labels, overlap, fetchability) and runs the CISA section-classifier over their real headings |

## Running them

Prerequisites: the `glokta` conda environment. Notebooks are **DB-free** (pure functions + an
optional model call) — no Postgres or Prefect needed.

```bash
# install a notebook runner into the env if you don't have one
conda run -n glokta pip install jupyterlab

# launch from the repo root
conda run -n glokta jupyter lab docs/walkthrough
```

**Live vs offline model calls.** Each notebook's setup cell loads the repo `.env` and sets
`LIVE = bool(HF_TOKEN)`:

- **`HF_TOKEN` present** → real inference against `huggingface/meta-llama/Llama-3.1-8B-Instruct`
  via the HF router (the same `inference.complete` the pipeline uses). A live call that fails falls
  back to a canned response so the notebook keeps flowing.
- **No `HF_TOKEN`** → a canned, representative response is used, so every notebook still runs and
  illustrates the scoring offline.

The scoring/parsing cells are fully deterministic either way.

## SYN pilot notebook (07)

`07_syn_pilot_recent_advisories.ipynb` is the input-reconstruction **leakage gate** in notebook
form (the transparent equivalent of `run_syn_pilot`). It collects real advisories from **CISA
(AA-series, from the disk cache) + CCCS + NCSC + The DFIR Report** (the latter three fetched live
via the multi-source connector `fetch_source_index` / `parse_source_advisory`; DFIR is enumerated
from its RSS feed), with **Malpedia** enriching the actor alias graph alongside the MISP galaxy.
It then **dedupes across sources** with `dedupe_advisories` (CVE-set fingerprint, keeping
`cisa > cccs > ncsc > dfir`), reconstructs the model inputs, measures whether the synthesised
conclusions (actor / technique) leak into those inputs, then runs and scores the model. Source
suitability for the national CERTs is reviewed in `08_source_review_national_certs.ipynb`.

**Populate the cache first** (downloads the latest 100 AA advisories to `data/cisa_advisories/`,
gitignored):

```bash
PYTHONPATH=src conda run -n glokta python scripts/download_cisa_advisories.py 100
```

Env knobs: `SYN_PILOT_MAX` (advisories, default 5), `SYN_PILOT_GALAXY` (`1`/`0`, fetch the actor
alias graph), `SYN_PILOT_MASK` (`1`/`0`, apply the leakage policy). If the cache is empty the
notebook fetches a small live index; if nothing reconstructs it falls back to two labelled fixtures.

> Finding on real data: AA advisories inline the ATT&CK technique ids in their Technical Details /
> tactic narrative, so **raw leak runs 30–100%** of conclusion claims. The **hybrid policy** fixes
> it: `mask_conclusions` strips the technique-ids + actor name/aliases (keeping the behavioural
> evidence), and `build_syn_item(mask=True)` **drops** any advisory that still leaks or goes too
> thin. The pilot shows raw-leak → **0** on the kept set. With the labels masked, recall reflects
> genuine *synthesis* (a small model can no longer copy) — which is what SYN should measure. The
> policy is wired through `ingest_syn_items(..., mask=True, alias_index=...)`.

## Regenerating

The notebooks are generated from a single script so they stay in sync with the code:

```bash
PYTHONPATH=src conda run -n glokta python scripts/build_walkthrough_notebooks.py
```

## Notes

- Reference data (ATT&CK technique index, MISP-galaxy alias/related graph) is inlined as small
  in-memory dicts to keep the notebooks DB-free; in production these come from the reference tables
  via `build_technique_index()` / `build_taa_indices()`.
- ATE/TAA/SYN use a clean synthetic advisory. Live CISA pages arrive as raw HTML and need an
  HTML→text + section extractor before they're production-ready (see CTI Benchmark.md §6).
- SYN is **enabled** in production (its leakage gate is enforced at ingest via masking); this
  walkthrough injects a stub faithfulness judge in place of the production LLM judge
  (`CTI_JUDGE_MODEL`).
