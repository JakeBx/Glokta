#!/usr/bin/env python3
"""Minimal live CTI smoke run: ingest a tiny recent slice, then score each benchmark.

Drives the CTI pipeline directly (no Prefect) against the configured Postgres so a minimal
end-to-end run for every task lands in the DB. Per-connector failures are tolerated; if CISA
advisories can't be fetched (bot-blocking), a clearly-labelled synthetic advisory seeds the
ATE/TAA/SYN path so those benchmarks still produce a scored result.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/run_cti_smoke.py

Env knobs: CTI_SMOKE_MAX_ITEMS (default 2), CTI_SMOKE_CVE_RECORDS (default 5),
CTI_INGEST_LOOKBACK_DAYS (default 2).
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

import glokta.infrastructure.db.orm  # noqa: F401
from glokta.application.cti.eval_service import execute_cti_run
from glokta.application.cti.forecast_service import (
    resolve_forecast_labels,
    seed_forecast_items,
    upsert_kev_entries,
)
from glokta.application.cti.ingest_service import ingest_cve_records, ingest_items
from glokta.application.cti.reference_service import (
    build_taa_indices,
    upsert_attack_techniques,
    upsert_threat_actors,
)
from glokta.application.cti.syn_service import ingest_syn_items, run_syn_pilot
from glokta.config import settings
from glokta.infrastructure.cti.connectors.attack import (
    fetch_attack_bundle,
    normalise_attack_bundle,
)
from glokta.infrastructure.cti.connectors.cve import fetch_recent_cve_records
from glokta.infrastructure.cti.connectors.galaxy import (
    fetch_galaxy_cluster,
    normalise_galaxy,
)
from glokta.infrastructure.cti.connectors.kev import fetch_kev
from glokta.infrastructure.cti.connectors.report import (
    detect_actor,
    fetch_advisory_feed,
    normalise_report,
    parse_advisory,
)
from glokta.infrastructure.db.orm import CtiItem, CtiResult, CtiRun, Model
from glokta.infrastructure.db.session import SessionLocal, init_db

MODEL_NAME = "huggingface/meta-llama/Llama-3.1-8B-Instruct"
MAX_ITEMS = int(os.environ.get("CTI_SMOKE_MAX_ITEMS", "2"))
CVE_RECORDS = int(os.environ.get("CTI_SMOKE_CVE_RECORDS", "5"))
LOOKBACK = settings.cti_ingest_lookback_days
HEADERS = {"User-Agent": "glokta-cti-smoke/0.1"}

# A synthetic AA-series advisory used only if the live CISA feed is unavailable.
SYNTHETIC_ADVISORY_TEXT = """\
Summary
APT29 conducted an espionage campaign attributed with high confidence to Russian SVR.

Technical Details
The actors used T1059 (Command and Scripting Interpreter) and T1566 (Phishing). The campaign
exploited CVE-2024-3400. Beaconing was observed to 198.51.100.23.

Indicators of Compromise
198.51.100.23

MITRE ATT&CK Techniques
T1059
T1566

Mitigations
Apply vendor patches and enforce MFA.
"""


def log(step, msg):
    print(f"[{step:9}] {msg}", flush=True)


def _client():
    return httpx.Client(timeout=60.0, headers=HEADERS)


def ingest_reference(db):
    try:
        with _client() as c:
            techs = normalise_attack_bundle(fetch_attack_bundle(c))
        n = upsert_attack_techniques(db, techs)
        log("attack", f"techniques upserted: {n}")
    except Exception as exc:
        log("attack", f"SKIPPED ({type(exc).__name__}: {str(exc)[:80]})")
    try:
        with _client() as c:
            actors = normalise_galaxy(fetch_galaxy_cluster(c))
        n = upsert_threat_actors(db, actors)
        log("galaxy", f"actors upserted: {n}")
    except Exception as exc:
        log("galaxy", f"SKIPPED ({type(exc).__name__}: {str(exc)[:80]})")


def ingest_cve_and_forecast(db):
    try:
        with _client() as c:
            records = fetch_recent_cve_records(
                c, lookback_days=LOOKBACK, headers=HEADERS, max_records=CVE_RECORDS
            )
        res = ingest_cve_records(db, records, source_revision="smoke")
        seeded = seed_forecast_items(db, records, "smoke")
        log("cve", f"records={len(records)} rcm/vsp ingest={res} forecast_seeded={seeded}")
    except Exception as exc:
        log("cve", f"FAILED ({type(exc).__name__}: {str(exc)[:100]})")


def ingest_kev(db):
    try:
        with _client() as c:
            entries = fetch_kev(c)
        inserted = upsert_kev_entries(db, entries)
        resolved = resolve_forecast_labels(db, entries)
        log("kev", f"entries={len(entries)} inserted={inserted} forecast_resolved={resolved}")
    except Exception as exc:
        log("kev", f"SKIPPED ({type(exc).__name__}: {str(exc)[:80]})")


def ingest_advisories(db):
    """ATE/TAA/SYN items.

    Live CISA pages are fetched only as a connectivity check: they arrive as raw HTML, and a
    proper HTML->text + section extractor is a known remaining gap. To validate the ATE/TAA/SYN
    *pipeline* end-to-end we ingest a single clean synthetic advisory.
    """
    alias_index, _ = build_taa_indices(db)
    try:
        with _client() as c:
            entries = fetch_advisory_feed(c, lookback_days=max(LOOKBACK, 30), headers=HEADERS)
        log("report", f"live CISA feed reachable: {len(entries)} advisories in window "
                      "(raw-HTML parsing deferred; using synthetic advisory for items)")
    except Exception as exc:
        log("report", f"live CISA feed unreachable ({type(exc).__name__}: {str(exc)[:60]})")

    synth = parse_advisory(
        {"title": "AA99-001A: Synthetic Smoke Advisory", "link": "synthetic",
         "published_date": datetime.now(timezone.utc).date()},
        SYNTHETIC_ADVISORY_TEXT,
    )
    synth["actor"] = detect_actor(synth["text"], alias_index) or "APT29"

    log("report", f"ATE/TAA ingest: {ingest_items(db, normalise_report(synth))}")
    # SYN: deterministic claim set (judge_infer=None) to avoid an external judge dependency.
    log("syn", f"SYN ingest: {ingest_syn_items(db, [synth], judge_infer=None)}")


def run_task(db, model_name, task):
    run = CtiRun(model_id=_model_id(db), task=task, status="running",
                 started_at=datetime.now(timezone.utc))
    db.add(run)
    db.flush()
    try:
        result = execute_cti_run(str(run.id), model_name, task, db, max_items=MAX_ITEMS)
        run.status = "complete"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        log("eval", f"{task}: {result} prequential={run.prequential_score}")
    except Exception as exc:
        run.status = "failed"
        db.commit()
        log("eval", f"{task}: FAILED ({type(exc).__name__}: {str(exc)[:100]})")


def _model_id(db):
    return db.query(Model).filter(Model.name == MODEL_NAME).one().id


def reset_cti(db):
    """Clear prior CTI runs/results/items so each smoke run is clean (refs/KEV kept)."""
    db.query(CtiResult).delete()
    db.query(CtiRun).delete()
    db.query(CtiItem).delete()
    db.commit()


def main():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Model).filter(Model.name == MODEL_NAME).first() is None:
            raise SystemExit("Model not seeded — run scripts/seed_cti_model.py first")

        reset_cti(db)
        log("start", f"model={MODEL_NAME} max_items/run={MAX_ITEMS} lookback={LOOKBACK}d")
        ingest_reference(db)
        ingest_cve_and_forecast(db)
        ingest_kev(db)
        ingest_advisories(db)

        counts = {
            t: db.query(CtiItem).filter(CtiItem.task == t, CtiItem.status == "active").count()
            for t in ("rcm", "vsp", "forecast", "ate", "taa", "syn")
        }
        log("items", str(counts))

        for task in ("rcm", "vsp", "forecast", "ate", "taa"):
            run_task(db, MODEL_NAME, task)

        # SYN is gated (never auto-queued); run the pilot manually with a stub judge so the
        # validation exercises recall/extraction without an external faithfulness judge.
        try:
            res = run_syn_pilot(db, MODEL_NAME, limit=MAX_ITEMS, judge=lambda claim, inputs: True)
            log("eval", f"syn (pilot): {res}")
        except Exception as exc:
            log("eval", f"syn (pilot): FAILED ({type(exc).__name__}: {str(exc)[:100]})")

        log("done", "smoke run complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
