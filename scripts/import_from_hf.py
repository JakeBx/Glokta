#!/usr/bin/env python3
"""
Import a Glokta HuggingFace dataset into the local database (idempotent merge).

Pulls the dataset from HF_DATASET_REPO and performs an idempotent merge:
  - models      matched by primary key id       → skip on conflict, insert on miss
  - runs        matched by primary key id       → skip on conflict, insert on miss
  - probe_results matched by (run_id, probe_name, detector) → skip on conflict, insert on miss

Import order respects FK dependencies: models → runs → probe_results → attempts.

Usage (conda dev env):
    PYTHONPATH=src conda run -n glokta python scripts/import_from_hf.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/import_from_hf.py

Dry-run (no DB writes):
    PYTHONPATH=src python scripts/import_from_hf.py --dry-run

Required env vars:
    HF_DATASET_REPO  — e.g. "your-username/open-llm-sec-leaderboard"
    HF_TOKEN         — HuggingFace read API token (only required for private repos)

Optional env vars (resolved via .env):
    DATABASE_URL     — required; set via .env or environment variable (no default in code)
"""

import sys
import os
import argparse
import uuid
from datetime import datetime, date

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.database import SessionLocal, init_db, migrate_db
from glokta.models import Model, Run, ProbeResult, Attempt


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string, returning None for null/empty values."""
    if not value:
        return None
    # Handle both naive and tz-aware ISO strings
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO 8601 date string, returning None for null/empty values."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])  # take only the date portion
    except ValueError:
        return None


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    """Parse a UUID string, returning None for null/empty values."""
    if not value:
        return None
    return uuid.UUID(str(value))


def import_models(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    """
    Idempotent merge of model rows.
    Match key: id (UUID primary key).
    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    # Build a set of existing model IDs for fast lookup
    existing_ids = {str(m.id) for m in session.query(Model.id).all()}

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue

        if not dry_run:
            model = Model(
                id=_parse_uuid(row["id"]),
                name=row["name"],
                provider=row["provider"],
                version=row.get("version"),
                snapshot_date=_parse_date(row.get("snapshot_date")),
                is_active=row.get("is_active", True),
                created_at=_parse_datetime(row.get("created_at")),
            )
            session.add(model)
        inserted += 1

    if not dry_run:
        session.commit()

    return inserted, skipped


def import_runs(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    """
    Idempotent merge of run rows.
    Match key: id (UUID primary key).
    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    existing_ids = {str(r.id) for r in session.query(Run.id).all()}

    for row in rows:
        row_id = row["id"]
        if row_id in existing_ids:
            skipped += 1
            continue

        if not dry_run:
            run = Run(
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
            )
            session.add(run)
        inserted += 1

    if not dry_run:
        session.commit()

    return inserted, skipped


def import_probe_results(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    """
    Idempotent merge of probe_result rows.
    Match key: (run_id, probe_name, detector) composite natural key.
    The auto-increment id is NOT used for matching — a fresh id is assigned on insert.
    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    # Build a set of existing composite keys for fast lookup
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
            pr = ProbeResult(
                # id is intentionally omitted — let the DB assign a fresh auto-increment value
                run_id=_parse_uuid(row["run_id"]),
                probe_name=row["probe_name"],
                probe_category=row["probe_category"],
                detector=row["detector"],
                pass_count=row.get("pass_count", 0),
                fail_count=row.get("fail_count", 0),
                score=row.get("score"),
                created_at=_parse_datetime(row.get("created_at")),
            )
            session.add(pr)
        inserted += 1

    if not dry_run:
        session.commit()

    return inserted, skipped


def import_attempts(session, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    """
    Idempotent merge of attempt rows.
    Match key: id (integer primary key exported from source DB).
    Returns (inserted, skipped).
    """
    import json
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
            attempt = Attempt(
                id=row_id,
                run_id=_parse_uuid(row["run_id"]),
                probe_name=row["probe_name"],
                prompt=row.get("prompt"),
                response=row.get("response"),
                detector_outcome=detector_outcome,
                created_at=_parse_datetime(row.get("created_at")),
            )
            session.add(attempt)
        inserted += 1

    if not dry_run:
        session.commit()

    return inserted, skipped


def dataset_split_to_rows(split) -> list[dict]:
    """Convert a HuggingFace Dataset split to a list of dicts."""
    return [split[i] for i in range(len(split))]


def main():
    parser = argparse.ArgumentParser(
        description="Import a Glokta HuggingFace dataset into the local DB (idempotent merge)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and report what would be inserted/skipped without writing to the DB",
    )
    args = parser.parse_args()

    hf_repo = settings.hf_dataset_repo
    hf_token = settings.hf_token or None  # convert empty string to None

    if not hf_repo:
        print("✗ HF_DATASET_REPO is not set. Add it to your .env file or export it as an environment variable.")
        sys.exit(1)

    print("Glokta — Importing HuggingFace dataset into database...")
    print(f"  Source repo: {hf_repo}")
    if args.dry_run:
        print("  Mode: dry-run (no DB writes)")
    print()

    print("  Loading dataset from HuggingFace Hub...", end=" ", flush=True)
    try:
        from datasets import load_dataset
        # Each table was pushed as a separate named config (config_name).
        # Load them independently so different schemas are handled correctly.
        models_ds = load_dataset(hf_repo, name="models", token=hf_token)
        runs_ds = load_dataset(hf_repo, name="runs", token=hf_token)
        probe_results_ds = load_dataset(hf_repo, name="probe_results", token=hf_token)
        attempts_ds = load_dataset(hf_repo, name="attempts", token=hf_token)
    except Exception as e:
        print(f"\n✗ Failed to load dataset: {e}")
        sys.exit(1)
    print("done")

    # Each load_dataset() returns a DatasetDict with a single "train" split
    # (default when no split name was specified during push_to_hub).
    model_rows = dataset_split_to_rows(models_ds["train"])
    run_rows = dataset_split_to_rows(runs_ds["train"])
    probe_result_rows = dataset_split_to_rows(probe_results_ds["train"])
    attempt_rows = dataset_split_to_rows(attempts_ds["train"])

    print(f"  Downloaded: {len(model_rows)} models, {len(run_rows)} runs, {len(probe_result_rows)} probe_results, {len(attempt_rows)} attempts")
    print()

    init_db()
    migrate_db()
    session = SessionLocal()
    try:
        # Import in FK dependency order: models → runs → probe_results
        print("  Merging models...", end=" ", flush=True)
        m_inserted, m_skipped = import_models(session, model_rows, args.dry_run)
        print(f"inserted={m_inserted}, skipped={m_skipped}")

        print("  Merging runs...", end=" ", flush=True)
        r_inserted, r_skipped = import_runs(session, run_rows, args.dry_run)
        print(f"inserted={r_inserted}, skipped={r_skipped}")

        print("  Merging probe_results...", end=" ", flush=True)
        pr_inserted, pr_skipped = import_probe_results(session, probe_result_rows, args.dry_run)
        print(f"inserted={pr_inserted}, skipped={pr_skipped}")

        print("  Merging attempts...", end=" ", flush=True)
        a_inserted, a_skipped = import_attempts(session, attempt_rows, args.dry_run)
        print(f"inserted={a_inserted}, skipped={a_skipped}")
    except Exception as e:
        session.rollback()
        print(f"\n✗ Import failed: {e}")
        sys.exit(1)
    finally:
        session.close()

    print()
    if args.dry_run:
        print("✓ Dry-run complete — no data was written to the database.")
    else:
        print("✓ Import complete.")

    print()
    print("Summary:")
    print(f"  models:        inserted={m_inserted}, skipped={m_skipped}")
    print(f"  runs:          inserted={r_inserted}, skipped={r_skipped}")
    print(f"  probe_results: inserted={pr_inserted}, skipped={pr_skipped}")
    print(f"  attempts:      inserted={a_inserted}, skipped={a_skipped}")


if __name__ == "__main__":
    main()
