#!/usr/bin/env python3
"""
Export the Glokta CTI benchmark tables to a HuggingFace dataset.

Exports seven configs — models, cti_items, cti_runs, cti_results,
cti_attack_techniques, cti_threat_actors, cti_kev — each as a separate
named config pushed to HF_CTI_DATASET_REPO.

Usage (conda dev env):
    PYTHONPATH=src conda run -n glokta python scripts/export_cti_to_hf.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/export_cti_to_hf.py

Dry-run (no upload):
    PYTHONPATH=src python scripts/export_cti_to_hf.py --dry-run

Required env vars:
    HF_CTI_DATASET_REPO  — e.g. "JakeBx/glokta-cti"
    HF_TOKEN             — HuggingFace write API token

Optional env vars (resolved via .env):
    DATABASE_URL         — required; set via .env or environment variable (no default in code)
"""

import json
import os
import sys
import argparse

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.infrastructure.db.session import SessionLocal, init_db, migrate_db
from glokta.infrastructure.db.orm import (
    CtiAttackTechnique,
    CtiItem,
    CtiKev,
    CtiResult,
    CtiRun,
    CtiThreatActor,
    Model,
)

from dotenv import load_dotenv

load_dotenv()


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
            "cutoff_start": _date_to_str(m.cutoff_start),
            "cutoff_end": _date_to_str(m.cutoff_end),
            "created_at": _date_to_str(m.created_at),
        })
    return rows


def export_cti_items(session) -> list[dict]:
    rows = []
    for item in session.query(CtiItem).order_by(CtiItem.first_available_date, CtiItem.id).all():
        rows.append({
            "id": str(item.id),
            "task": item.task,
            "external_id": item.external_id,
            "source": item.source,
            "input_text": item.input_text,
            "input_ref": json.dumps(item.input_ref) if item.input_ref is not None else None,
            "label": json.dumps(item.label) if item.label is not None else None,
            "label_provenance": json.dumps(item.label_provenance) if item.label_provenance is not None else None,
            "source_revision": item.source_revision,
            "input_date": _date_to_str(item.input_date),
            "label_date": _date_to_str(item.label_date),
            "first_available_date": _date_to_str(item.first_available_date),
            "authority_agreement": item.authority_agreement,
            "withhold": item.withhold,
            "status": item.status,
            "created_at": _date_to_str(item.created_at),
        })
    return rows


def export_cti_runs(session) -> list[dict]:
    rows = []
    for run in session.query(CtiRun).order_by(CtiRun.created_at).all():
        rows.append({
            "id": str(run.id),
            "model_id": str(run.model_id),
            "task": run.task,
            "triggered_by": run.triggered_by,
            "status": run.status,
            "model_cutoff_start": _date_to_str(run.model_cutoff_start),
            "model_cutoff_end": _date_to_str(run.model_cutoff_end),
            "item_count": run.item_count,
            "scored_count": run.scored_count,
            "prequential_score": run.prequential_score,
            "config": json.dumps(run.config) if run.config is not None else None,
            "started_at": _date_to_str(run.started_at),
            "completed_at": _date_to_str(run.completed_at),
            "created_at": _date_to_str(run.created_at),
        })
    return rows


def export_cti_results(session) -> list[dict]:
    rows = []
    for r in session.query(CtiResult).order_by(CtiResult.run_id, CtiResult.id).all():
        rows.append({
            "id": r.id,
            "run_id": str(r.run_id),
            "item_id": str(r.item_id),
            "model_id": str(r.model_id),
            "prompt_hash": r.prompt_hash,
            "score": r.score,
            "score_breakdown": json.dumps(r.score_breakdown) if r.score_breakdown is not None else None,
            "correct": r.correct,
            "pre_cutoff": r.pre_cutoff,
            "created_at": _date_to_str(r.created_at),
        })
    return rows


def export_cti_attack_techniques(session) -> list[dict]:
    rows = []
    for t in session.query(CtiAttackTechnique).order_by(CtiAttackTechnique.technique_id).all():
        rows.append({
            "id": t.id,
            "technique_id": t.technique_id,
            "name": t.name,
            "tactic": t.tactic,
            "revoked_by": t.revoked_by,
            "version": t.version,
        })
    return rows


def export_cti_threat_actors(session) -> list[dict]:
    rows = []
    for a in session.query(CtiThreatActor).order_by(CtiThreatActor.canonical_name).all():
        rows.append({
            "id": a.id,
            "canonical_name": a.canonical_name,
            "aliases": json.dumps(a.aliases) if a.aliases is not None else None,
            "techniques": json.dumps(a.techniques) if a.techniques is not None else None,
            "related_groups": json.dumps(a.related_groups) if a.related_groups is not None else None,
        })
    return rows


def export_cti_kev(session) -> list[dict]:
    rows = []
    for k in session.query(CtiKev).order_by(CtiKev.date_added, CtiKev.cve_id).all():
        rows.append({
            "id": k.id,
            "cve_id": k.cve_id,
            "date_added": _date_to_str(k.date_added),
            "vendor": k.vendor,
            "product": k.product,
        })
    return rows


def rows_to_dataset(rows: list[dict]):
    from datasets import Dataset

    if not rows:
        return Dataset.from_dict({})
    columns = list(rows[0].keys())
    data = {col: [row[col] for row in rows] for col in columns}
    return Dataset.from_dict(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Glokta CTI benchmark tables to a HuggingFace dataset"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Serialise and print row counts without pushing to HuggingFace",
    )
    args = parser.parse_args()

    hf_repo = os.environ.get("HF_CTI_DATASET_REPO") or getattr(settings, "hf_cti_dataset_repo", None)
    hf_token = settings.hf_token

    if not args.dry_run:
        if not hf_repo:
            print("✗ HF_CTI_DATASET_REPO is not set. Add it to your .env file or export it as an environment variable.")
            sys.exit(1)
        if not hf_token:
            print("✗ HF_TOKEN is not set. Add it to your .env file or export it as an environment variable.")
            sys.exit(1)

    print("Glokta — Exporting CTI tables to HuggingFace dataset...")
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

        print("  Querying cti_items...", end=" ", flush=True)
        item_rows = export_cti_items(session)
        print(f"{len(item_rows)} rows")

        print("  Querying cti_runs...", end=" ", flush=True)
        run_rows = export_cti_runs(session)
        print(f"{len(run_rows)} rows")

        print("  Querying cti_results...", end=" ", flush=True)
        result_rows = export_cti_results(session)
        print(f"{len(result_rows)} rows")

        print("  Querying cti_attack_techniques...", end=" ", flush=True)
        technique_rows = export_cti_attack_techniques(session)
        print(f"{len(technique_rows)} rows")

        print("  Querying cti_threat_actors...", end=" ", flush=True)
        actor_rows = export_cti_threat_actors(session)
        print(f"{len(actor_rows)} rows")

        print("  Querying cti_kev...", end=" ", flush=True)
        kev_rows = export_cti_kev(session)
        print(f"{len(kev_rows)} rows")
    finally:
        session.close()

    print()
    print("  Building DatasetDict...", end=" ", flush=True)
    from datasets import DatasetDict

    dataset_dict = DatasetDict({
        "models": rows_to_dataset(model_rows),
        "cti_items": rows_to_dataset(item_rows),
        "cti_runs": rows_to_dataset(run_rows),
        "cti_results": rows_to_dataset(result_rows),
        "cti_attack_techniques": rows_to_dataset(technique_rows),
        "cti_threat_actors": rows_to_dataset(actor_rows),
        "cti_kev": rows_to_dataset(kev_rows),
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
    for config_name, ds in dataset_dict.items():
        print(f"  Pushing config '{config_name}' ({len(ds)} rows) ...", end=" ", flush=True)
        ds.push_to_hub(hf_repo, config_name=config_name, token=hf_token)
        print("done")

    print()
    print(f"✓ Dataset pushed to https://huggingface.co/datasets/{hf_repo}")
    print(f"  models:                {len(model_rows)} rows")
    print(f"  cti_items:             {len(item_rows)} rows")
    print(f"  cti_runs:              {len(run_rows)} rows")
    print(f"  cti_results:           {len(result_rows)} rows")
    print(f"  cti_attack_techniques: {len(technique_rows)} rows")
    print(f"  cti_threat_actors:     {len(actor_rows)} rows")
    print(f"  cti_kev:               {len(kev_rows)} rows")


if __name__ == "__main__":
    main()
