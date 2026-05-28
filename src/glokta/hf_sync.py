"""HuggingFace Dataset sync — import Glokta data into the local database.

Provides import_all() for programmatic use (e.g. HF Space startup)
and the helpers used by the scripts/import_from_hf.py CLI.
"""

import json
import logging
import uuid
from datetime import datetime, date

log = logging.getLogger(__name__)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    return uuid.UUID(str(value))


def dataset_split_to_rows(split) -> list[dict]:
    return [split[i] for i in range(len(split))]


def import_models(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    from glokta.models import Model
    inserted = 0
    skipped = 0
    existing_ids = {str(m.id) for m in session.query(Model.id).all()}
    for row in rows:
        if row["id"] in existing_ids:
            skipped += 1
            continue
        if not dry_run:
            session.add(Model(
                id=_parse_uuid(row["id"]),
                name=row["name"],
                provider=row["provider"],
                version=row.get("version"),
                snapshot_date=_parse_date(row.get("snapshot_date")),
                is_active=row.get("is_active", True),
                created_at=_parse_datetime(row.get("created_at")),
            ))
        inserted += 1
    if not dry_run:
        session.commit()
    return inserted, skipped


def import_runs(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    from glokta.models import Run
    inserted = 0
    skipped = 0
    existing_ids = {str(r.id) for r in session.query(Run.id).all()}
    for row in rows:
        if row["id"] in existing_ids:
            skipped += 1
            continue
        if not dry_run:
            session.add(Run(
                id=_parse_uuid(row["id"]),
                model_id=_parse_uuid(row["model_id"]),
                triggered_by=row.get("triggered_by", "import"),
                status=row.get("status", "complete"),
                started_at=_parse_datetime(row.get("started_at")),
                completed_at=_parse_datetime(row.get("completed_at")),
                created_at=_parse_datetime(row.get("created_at")),
                garak_version=row.get("garak_version"),
                scanned_at=_parse_datetime(row.get("scanned_at")),
                submitted_by=row.get("submitted_by"),
                garak_config=row.get("garak_config"),
                config_hash=row.get("config_hash"),
                jsonl_manifest_hash=row.get("jsonl_manifest_hash"),
                verification_requested_at=_parse_datetime(row.get("verification_requested_at")),
                source_community_run_id=_parse_uuid(row.get("source_community_run_id")),
            ))
        inserted += 1
    if not dry_run:
        session.commit()
    return inserted, skipped


def import_probe_results(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    from glokta.models import ProbeResult
    inserted = 0
    skipped = 0
    existing_keys = {
        (str(pr.run_id), pr.probe_name, pr.detector)
        for pr in session.query(ProbeResult.run_id, ProbeResult.probe_name, ProbeResult.detector).all()
    }
    for row in rows:
        key = (row["run_id"], row["probe_name"], row["detector"])
        if key in existing_keys:
            skipped += 1
            continue
        if not dry_run:
            session.add(ProbeResult(
                run_id=_parse_uuid(row["run_id"]),
                probe_name=row["probe_name"],
                probe_category=row["probe_category"],
                detector=row["detector"],
                pass_count=row.get("pass_count", 0),
                fail_count=row.get("fail_count", 0),
                score=row.get("score"),
                created_at=_parse_datetime(row.get("created_at")),
            ))
        inserted += 1
    if not dry_run:
        session.commit()
    return inserted, skipped


def import_attempts(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    from glokta.models import Attempt
    inserted = 0
    skipped = 0
    existing_ids = {a.id for a in session.query(Attempt.id).all()}
    for row in rows:
        row_id = int(row["id"])
        if row_id in existing_ids:
            skipped += 1
            continue
        if not dry_run:
            raw_outcome = row.get("detector_outcome")
            detector_outcome = json.loads(raw_outcome) if isinstance(raw_outcome, str) else raw_outcome
            session.add(Attempt(
                id=row_id,
                run_id=_parse_uuid(row["run_id"]),
                probe_name=row["probe_name"],
                prompt=row.get("prompt"),
                response=row.get("response"),
                detector_outcome=detector_outcome,
                created_at=_parse_datetime(row.get("created_at")),
            ))
        inserted += 1
    if not dry_run:
        session.commit()
    return inserted, skipped


def import_all(dry_run: bool = False) -> None:
    """Import (or refresh) the local database from the configured HF Dataset.

    Safe to call on startup: performs an idempotent merge, so existing rows
    are skipped. Raises RuntimeError if HF_DATASET_REPO is not set or download fails.
    """
    from glokta.config import settings
    from glokta.database import SessionLocal, init_db, migrate_db

    hf_repo = settings.hf_dataset_repo
    hf_token = settings.hf_token or None

    if not hf_repo:
        raise RuntimeError("HF_DATASET_REPO is not set.")

    log.info("import_all: loading dataset from %s", hf_repo)
    try:
        from datasets import load_dataset
        models_ds = load_dataset(hf_repo, name="models", token=hf_token)
        runs_ds = load_dataset(hf_repo, name="runs", token=hf_token)
        probe_results_ds = load_dataset(hf_repo, name="probe_results", token=hf_token)
        attempts_ds = load_dataset(hf_repo, name="attempts", token=hf_token)
    except Exception as e:
        raise RuntimeError(f"Failed to load HF dataset '{hf_repo}': {e}") from e

    model_rows = dataset_split_to_rows(models_ds["train"])
    run_rows = dataset_split_to_rows(runs_ds["train"])
    probe_result_rows = dataset_split_to_rows(probe_results_ds["train"])
    attempt_rows = dataset_split_to_rows(attempts_ds["train"])

    log.info(
        "import_all: downloaded %d models, %d runs, %d probe_results, %d attempts",
        len(model_rows), len(run_rows), len(probe_result_rows), len(attempt_rows),
    )

    init_db()
    migrate_db()
    session = SessionLocal()
    try:
        m_ins, m_skip = import_models(session, model_rows, dry_run)
        r_ins, r_skip = import_runs(session, run_rows, dry_run)
        pr_ins, pr_skip = import_probe_results(session, probe_result_rows, dry_run)
        a_ins, a_skip = import_attempts(session, attempt_rows, dry_run)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    log.info(
        "import_all: models(+%d/=%d) runs(+%d/=%d) probe_results(+%d/=%d) attempts(+%d/=%d)",
        m_ins, m_skip, r_ins, r_skip, pr_ins, pr_skip, a_ins, a_skip,
    )
