"""Prefect flows for the CTI living benchmark.

Thin Prefect adapters — all business logic lives in application/cti/*. Gated by
settings.cti_enabled so they are inert until CTI is switched on.
"""

import logging
from datetime import datetime, timezone

import httpx
from prefect import flow, task

from glokta.application.cti.eval_service import (
    execute_cti_run,
    process_pending_cti_run,
    queue_cti_runs,
)
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
from glokta.application.cti.syn_service import ingest_syn_items
from glokta.infrastructure.cti.inference import complete
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
from glokta.infrastructure.cti.cutoffs import apply_cutoffs
from glokta.infrastructure.db.orm import CtiRun
from glokta.infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)

_RETRY_DELAYS: list[float] = [30, 60, 120]
# A descriptive UA — CISA/raw endpoints 403 naïve clients.
_HTTP_HEADERS = {"User-Agent": "glokta-cti/0.1 (+https://github.com/JakeBx/glokta)"}


@task(
    name="execute-cti-run",
    retries=3,
    retry_delay_seconds=_RETRY_DELAYS,
    timeout_seconds=settings.cti_run_timeout_seconds,
)
def execute_cti_run_task(run_id: str, model_name: str, task_name: str) -> dict:
    """Prefect task: evaluate one model against one CTI task, with retries."""
    db = SessionLocal()
    try:
        run = db.query(CtiRun).filter(CtiRun.id == run_id).first()
        if run is None or run.status != "running":
            raise RuntimeError(f"CTI run {run_id} not in 'running' state; skipping")
        result = execute_cti_run(run_id, model_name, task_name, db)
        db.refresh(run)
        run.status = "complete"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return result
    finally:
        db.close()


@flow(name="cti-ingest-cve", log_prints=True)
def cti_ingest_cve() -> None:
    """Ingest the recent CVE slice (RCM/VSP) via the cvelistV5 delta feed. Hourly."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        with httpx.Client(timeout=30.0, headers=_HTTP_HEADERS) as client:
            records = fetch_recent_cve_records(
                client, lookback_days=settings.cti_ingest_lookback_days
            )
        revision = (records[0].get("cveMetadata", {}).get("dateUpdated") if records else None)
        result = ingest_cve_records(
            db,
            records,
            revision,
            withhold_window_days=settings.cti_withhold_window_days,
        )
        seeded = seed_forecast_items(db, records, revision)
        logger.info("cti_ingest_cve: records=%d %s forecast_seeded=%d", len(records), result, seeded)
    except Exception as exc:
        logger.error("cti_ingest_cve failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-ingest-kev", log_prints=True)
def cti_ingest_kev() -> None:
    """Ingest CISA KEV and resolve Forecast labels (exploited=True). Daily."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        with httpx.Client(timeout=30.0, headers=_HTTP_HEADERS) as client:
            entries = fetch_kev(client)
        inserted = upsert_kev_entries(db, entries)
        resolved = resolve_forecast_labels(db, entries)
        logger.info("cti_ingest_kev: entries=%d inserted=%d resolved=%d", len(entries), inserted, resolved)
    except Exception as exc:
        logger.error("cti_ingest_kev failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-ingest-attack", log_prints=True)
def cti_ingest_attack() -> None:
    """Refresh the ATT&CK technique reference table. Weekly."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        with httpx.Client(timeout=60.0, headers=_HTTP_HEADERS) as client:
            bundle = fetch_attack_bundle(client)
        count = upsert_attack_techniques(db, normalise_attack_bundle(bundle))
        logger.info("cti_ingest_attack: techniques=%d", count)
    except Exception as exc:
        logger.error("cti_ingest_attack failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-ingest-galaxy", log_prints=True)
def cti_ingest_galaxy() -> None:
    """Refresh the threat-actor reference table (aliases + related groups). Weekly."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        with httpx.Client(timeout=60.0, headers=_HTTP_HEADERS) as client:
            cluster = fetch_galaxy_cluster(client)
        count = upsert_threat_actors(db, normalise_galaxy(cluster))
        logger.info("cti_ingest_galaxy: actors=%d", count)
    except Exception as exc:
        logger.error("cti_ingest_galaxy failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-ingest-report", log_prints=True)
def cti_ingest_report() -> None:
    """Ingest recent CISA advisories as ATE/TAA items. Daily."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        alias_index, _related = build_taa_indices(db)
        with httpx.Client(timeout=30.0, headers=_HTTP_HEADERS) as client:
            entries = fetch_advisory_feed(
                client, lookback_days=settings.cti_ingest_lookback_days
            )
            items = []
            for entry in entries:
                if not entry.get("link"):
                    continue
                page = client.get(entry["link"])
                advisory = parse_advisory(entry, getattr(page, "text", "") or "")
                advisory["actor"] = detect_actor(advisory["text"], alias_index)
                items.extend(normalise_report(advisory))
        result = ingest_items(
            db, items, withhold_window_days=settings.cti_withhold_window_days
        )
        logger.info("cti_ingest_report: advisories=%d %s", len(entries), result)
    except Exception as exc:
        logger.error("cti_ingest_report failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-ingest-syn", log_prints=True)
def cti_ingest_syn() -> None:
    """Ingest SYN items (claim-set labels + reconstructed inputs) from advisories. Gated; no eval queued."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        alias_index, _related = build_taa_indices(db)
        advisories = []
        with httpx.Client(timeout=30.0, headers=_HTTP_HEADERS) as client:
            for entry in fetch_advisory_feed(
                client, lookback_days=settings.cti_ingest_lookback_days
            ):
                if not entry.get("link"):
                    continue
                page = client.get(entry["link"])
                advisory = parse_advisory(entry, getattr(page, "text", "") or "")
                advisory["actor"] = detect_actor(advisory["text"], alias_index)
                advisories.append(advisory)
        result = ingest_syn_items(
            db,
            advisories,
            judge_infer=complete,
            withhold_window_days=settings.cti_withhold_window_days,
        )
        logger.info("cti_ingest_syn: advisories=%d %s", len(advisories), result)
    except Exception as exc:
        logger.error("cti_ingest_syn failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="cti-scan-pending", log_prints=True)
def cti_scan_pending() -> None:
    """Pick one pending CTI run and execute it. Scheduled frequently."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        process_pending_cti_run(
            db,
            lambda rid, mname, task_name: execute_cti_run_task(rid, mname, task_name),
        )
    finally:
        db.close()


@flow(name="cti-bootstrap", log_prints=True)
def cti_bootstrap() -> None:
    """One-shot: ingest the recent slice across all connectors, then queue + drain CTI runs.

    Runs connectors in dependency order (reference tables first), applies cutoffs, queues runs
    for the enabled non-SYN tasks, and drains them inline. For a live small-slice dry run.
    """
    if not settings.cti_enabled:
        logger.warning("cti_bootstrap: CTI disabled (set CTI_ENABLED=true)")
        return

    # Reference tables first, then dated items, then KEV resolution, then advisories.
    cti_ingest_attack()
    cti_ingest_galaxy()
    cti_ingest_cve()
    cti_ingest_kev()
    cti_ingest_report()
    cti_ingest_syn()

    db = SessionLocal()
    try:
        apply_cutoffs(db)
        queued = queue_cti_runs(
            db, tasks=["rcm", "vsp", "ate", "taa", "forecast"], scan_ttl_days=0
        )
        logger.info("cti_bootstrap: queued=%s", queued)
        guard = 0
        while (
            db.query(CtiRun).filter(CtiRun.status == "pending").count() > 0
            and guard < 10000
        ):
            process_pending_cti_run(
                db,
                lambda rid, mname, task_name: execute_cti_run(rid, mname, task_name, db),
            )
            guard += 1
        logger.info("cti_bootstrap: drained %d runs", guard)
    finally:
        db.close()


@flow(name="cti-trigger", log_prints=True)
def cti_trigger() -> None:
    """Queue pending CTI runs for active models with stale per-task coverage. Weekly."""
    if not settings.cti_enabled:
        return
    db = SessionLocal()
    try:
        apply_cutoffs(db)
        result = queue_cti_runs(db, scan_ttl_days=settings.scheduler_scan_ttl_days)
        logger.info("cti_trigger: queued=%d skipped=%d", result["queued"], result["skipped"])
    except Exception as exc:
        logger.error("cti_trigger failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()
