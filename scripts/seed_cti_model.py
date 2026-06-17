#!/usr/bin/env python3
"""Seed a single model for CTI benchmark validation.

Inserts (idempotently) the HF-hosted Llama-3.1-8B-Instruct as an active model, ensures the
CTI schema exists, and applies the curated cutoff map.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/seed_cti_model.py
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import glokta.infrastructure.db.orm  # noqa: F401  (populate Base.metadata)
from glokta.infrastructure.cti.cutoffs import apply_cutoffs
from glokta.infrastructure.db.orm import Model
from glokta.infrastructure.db.session import SessionLocal, init_db

MODEL_NAME = "huggingface/meta-llama/Llama-3.1-8B-Instruct"
PROVIDER = "meta-llama"


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(Model).filter(Model.name == MODEL_NAME).first()
        if existing is None:
            db.add(
                Model(
                    name=MODEL_NAME,
                    provider=PROVIDER,
                    version="3.1-8b-instruct",
                    snapshot_date=date.today(),
                    source="manual",
                    status="active",
                )
            )
            db.commit()
            print(f"seeded model: {MODEL_NAME}")
        else:
            existing.status = "active"
            db.commit()
            print(f"model already present (reactivated): {MODEL_NAME}")

        updated = apply_cutoffs(db)
        model = db.query(Model).filter(Model.name == MODEL_NAME).first()
        print(f"cutoffs applied: {updated} (this model: {model.cutoff_start} .. {model.cutoff_end})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
