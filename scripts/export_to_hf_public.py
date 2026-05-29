#!/usr/bin/env python3
"""
Export the Glokta database to a public HuggingFace dataset (no attempts).

Exports three tables — models, runs, probe_results — and pushes to
Jake/glokta-public (or HF_PUBLIC_DATASET_REPO if set).  Attempts are excluded
to keep the public dataset small and free of raw prompt/response content.

This is the dataset consumed by garkboard-lite.

Usage (conda dev env):
    PYTHONPATH=src conda run -n glokta python scripts/export_to_hf_public.py

Dry-run (no upload):
    PYTHONPATH=src python scripts/export_to_hf_public.py --dry-run

Required env vars:
    HF_TOKEN                — HuggingFace write API token

Optional env vars:
    HF_PUBLIC_DATASET_REPO  — defaults to "Jake/glokta-public"
    DATABASE_URL            — required; set via .env or environment variable
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.infrastructure.db.session import SessionLocal, init_db, migrate_db
from glokta.infrastructure.db.orm import Model, Run, ProbeResult

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_PUBLIC_REPO = "Jake/glokta-public"


def _date_to_str(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def export_models(session) -> list[dict]:
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


def rows_to_dataset(rows: list[dict]):
    from datasets import Dataset
    if not rows:
        return Dataset.from_dict({})
    columns = list(rows[0].keys())
    data = {col: [row[col] for row in rows] for col in columns}
    return Dataset.from_dict(data)


def main():
    parser = argparse.ArgumentParser(
        description="Export Glokta DB (no attempts) to Jake/glokta-public on HuggingFace"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Serialise and print row counts without pushing to HuggingFace")
    args = parser.parse_args()

    hf_repo = os.environ.get("HF_PUBLIC_DATASET_REPO", _DEFAULT_PUBLIC_REPO)
    hf_token = settings.hf_token

    if not args.dry_run:
        if not hf_token:
            print("✗ HF_TOKEN is not set.")
            sys.exit(1)

    print("Glokta — Exporting public dataset (no attempts) to HuggingFace...")
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
    finally:
        session.close()

    print()
    print("  Building DatasetDict...", end=" ", flush=True)
    from datasets import DatasetDict
    dataset_dict = DatasetDict({
        "models": rows_to_dataset(model_rows),
        "runs": rows_to_dataset(run_rows),
        "probe_results": rows_to_dataset(probe_result_rows),
    })
    print("done")

    if args.dry_run:
        print()
        print("Dry-run complete. Dataset splits:")
        for name, ds in dataset_dict.items():
            print(f"  {name}: {len(ds)} rows, columns: {ds.column_names}")
        print()
        print("✓ No data was pushed to HuggingFace (dry-run mode).")
        return

    print()
    for config_name, ds in dataset_dict.items():
        print(f"  Pushing config '{config_name}' ({len(ds)} rows) ...", end=" ", flush=True)
        ds.push_to_hub(hf_repo, config_name=config_name, token=hf_token)
        print("done")

    print()
    print(f"✓ Dataset pushed to https://huggingface.co/datasets/{hf_repo}")
    print(f"  models:        {len(model_rows)} rows")
    print(f"  runs:          {len(run_rows)} rows")
    print(f"  probe_results: {len(probe_result_rows)} rows")


if __name__ == "__main__":
    main()
