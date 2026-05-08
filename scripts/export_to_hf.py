#!/usr/bin/env python3
"""
Export the GarakBoard database to a HuggingFace dataset.

Exports four tables — models, runs, probe_results, attempts — as a multi-split DatasetDict
and pushes it to the HuggingFace Hub repository defined by HF_DATASET_REPO.

Usage (conda dev env):
    PYTHONPATH=src conda run -n garakboard python scripts/export_to_hf.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/export_to_hf.py

Dry-run (no upload):
    PYTHONPATH=src python scripts/export_to_hf.py --dry-run

Required env vars:
    HF_DATASET_REPO  — e.g. "your-username/open-llm-sec-leaderboard"
    HF_TOKEN         — HuggingFace write API token

Optional env vars (resolved via .env):
    DATABASE_URL     — required; set via .env or environment variable (no default in code)
"""

import sys
import os
import argparse

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garakboard.config import settings
from garakboard.database import SessionLocal, init_db, migrate_db
from garakboard.models import Model, Run, ProbeResult, Attempt


def _date_to_str(value) -> str | None:
    """Convert a date or datetime to an ISO 8601 string, or return None."""
    if value is None:
        return None
    return value.isoformat()


def export_models(session) -> list[dict]:
    """Serialise all rows from the models table."""
    rows = []
    for m in session.query(Model).order_by(Model.created_at).all():
        rows.append({
            "id": str(m.id),
            "name": m.name,
            "provider": m.provider,
            "version": m.version,
            "snapshot_date": _date_to_str(m.snapshot_date),
            "is_active": m.is_active,
            "created_at": _date_to_str(m.created_at),
        })
    return rows


def export_runs(session) -> list[dict]:
    """Serialise all rows from the runs table."""
    rows = []
    for r in session.query(Run).order_by(Run.created_at).all():
        rows.append({
            "id": str(r.id),
            "model_id": str(r.model_id),
            "triggered_by": r.triggered_by,
            "status": r.status,
            "started_at": _date_to_str(r.started_at),
            "completed_at": _date_to_str(r.completed_at),
            "created_at": _date_to_str(r.created_at),
            "garak_version": r.garak_version,
            "scanned_at": _date_to_str(r.scanned_at),
            "submitted_by": r.submitted_by,
            "garak_config": r.garak_config,
            "config_hash": r.config_hash,
            "jsonl_manifest_hash": r.jsonl_manifest_hash,
            "verification_requested_at": _date_to_str(r.verification_requested_at),
            "source_community_run_id": str(r.source_community_run_id) if r.source_community_run_id else None,
        })
    return rows


def export_probe_results(session) -> list[dict]:
    """Serialise all rows from the probe_results table."""
    rows = []
    for pr in session.query(ProbeResult).order_by(ProbeResult.run_id, ProbeResult.id).all():
        rows.append({
            "id": pr.id,
            "run_id": str(pr.run_id),
            "probe_name": pr.probe_name,
            "probe_category": pr.probe_category,
            "detector": pr.detector,
            "pass_count": pr.pass_count,
            "fail_count": pr.fail_count,
            "score": pr.score,
            "created_at": _date_to_str(pr.created_at),
        })
    return rows


def export_attempts(session) -> list[dict]:
    """Serialise all rows from the attempts table."""
    import json
    rows = []
    for a in session.query(Attempt).order_by(Attempt.run_id, Attempt.id).all():
        rows.append({
            "id": a.id,
            "run_id": str(a.run_id),
            "probe_name": a.probe_name,
            "prompt": a.prompt,
            "response": a.response,
            "detector_outcome": json.dumps(a.detector_outcome) if a.detector_outcome is not None else None,
            "created_at": _date_to_str(a.created_at),
        })
    return rows


def rows_to_dataset(rows: list[dict]):
    """Convert a list of dicts to a HuggingFace Dataset."""
    from datasets import Dataset

    if not rows:
        # Return an empty dataset rather than failing
        return Dataset.from_dict({})

    # Transpose list-of-dicts into dict-of-lists (HF Dataset format)
    columns = list(rows[0].keys())
    data = {col: [row[col] for row in rows] for col in columns}
    return Dataset.from_dict(data)


def main():
    parser = argparse.ArgumentParser(
        description="Export GarakBoard DB to a HuggingFace dataset"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Serialise and print row counts without pushing to HuggingFace",
    )
    args = parser.parse_args()

    hf_repo = settings.hf_dataset_repo
    hf_token = settings.hf_token

    if not args.dry_run:
        if not hf_repo:
            print("✗ HF_DATASET_REPO is not set. Add it to your .env file or export it as an environment variable.")
            sys.exit(1)
        if not hf_token:
            print("✗ HF_TOKEN is not set. Add it to your .env file or export it as an environment variable.")
            sys.exit(1)

    print("GarakBoard — Exporting database to HuggingFace dataset...")
    if not args.dry_run:
        print(f"  Target repo: {hf_repo}")
    print()

    init_db()
    migrate_db()
    session = SessionLocal()
    try:
        print("  Querying models...", end=" ", flush=True)
        model_rows = export_models(session)
        print(f"{len(model_rows)} rows")

        print("  Querying runs...", end=" ", flush=True)
        run_rows = export_runs(session)
        print(f"{len(run_rows)} rows")

        print("  Querying probe_results...", end=" ", flush=True)
        probe_result_rows = export_probe_results(session)
        print(f"{len(probe_result_rows)} rows")

        print("  Querying attempts...", end=" ", flush=True)
        attempt_rows = export_attempts(session)
        print(f"{len(attempt_rows)} rows")
    finally:
        session.close()

    print()
    print("  Building DatasetDict...", end=" ", flush=True)
    from datasets import DatasetDict

    dataset_dict = DatasetDict({
        "models": rows_to_dataset(model_rows),
        "runs": rows_to_dataset(run_rows),
        "probe_results": rows_to_dataset(probe_result_rows),
        "attempts": rows_to_dataset(attempt_rows),
    })
    print("done")

    if args.dry_run:
        print()
        print("Dry-run complete. Dataset splits:")
        for split_name, ds in dataset_dict.items():
            print(f"  {split_name}: {len(ds)} rows, columns: {ds.column_names}")
        print()
        print("✓ No data was pushed to HuggingFace (dry-run mode).")
        return

    print()
    # HuggingFace DatasetDict.push_to_hub() requires all splits to share the
    # same schema.  Since models/runs/probe_results have different schemas, we
    # push each table as a separate dataset configuration (named config) within
    # the same repo.  On the Hub this appears as three selectable configs.
    for config_name, ds in dataset_dict.items():
        print(f"  Pushing config '{config_name}' ({len(ds)} rows) ...", end=" ", flush=True)
        ds.push_to_hub(hf_repo, config_name=config_name, token=hf_token)
        print("done")

    print()
    print(f"✓ Dataset pushed to https://huggingface.co/datasets/{hf_repo}")
    print(f"  models:        {len(model_rows)} rows")
    print(f"  runs:          {len(run_rows)} rows")
    print(f"  probe_results: {len(probe_result_rows)} rows")
    print(f"  attempts:      {len(attempt_rows)} rows")


if __name__ == "__main__":
    main()
