#!/usr/bin/env python3
"""
Seed the GarakBoard database with OpenRouter free-tier models and probe run queue entries.

Usage (conda dev env):
    PYTHONPATH=src conda run -n garakboard python scripts/seed_models.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/seed_models.py

The script is idempotent: models that already exist (by name) are skipped, and probe
run queue entries that are already pending or running are skipped. Each seeded model
receives one ProbeRunQueue entry per probe category, covering 5 security dimensions.
"""

import sys
import os
from datetime import date
from collections import defaultdict
import uuid

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garakboard.database import SessionLocal, init_db
from garakboard.models import Model, ProbeRunQueue, Run
from garakboard.worker.tasks import publish_run_job

# Current OpenRouter free-tier model catalogue (verified April 2026)
# Format: (name, provider, version)
# All names use the openrouter/<provider>/<model>:free convention
TEST_MODELS = [
    ("openrouter/minimax/minimax-m2.5", "minimax", "m2.5"),
    ("openrouter/google/gemini-2.5-flash-lite", "google", "gemini-2.5-flash-lite"),
    ("openrouter/openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
    ("openrouter/mistralai/mistral-nemo", "mistralai", "nemo"),
]

# Garak probe categories for reasonable security coverage
# Covers: prompt injection, jailbreaking, harmful content, info leakage, safety alignment
PROBE_CATEGORIES = [
    "encoding",        # Prompt injection — encoding evasion (Base64, ROT13, etc.)
    "dan",             # Jailbreaking — DAN variants (DAN 11.0, DUDE, AIM)
    "goodside",        # Prompt injection — Riley Goodside techniques
    "promptinject",    # Prompt injection — HouYi framework
    "malwaregen",      # Harmful content — malware generation requests
    "continuation",    # Harmful content — toxic text continuation
    "lmrc",            # Safety alignment — Language Model Risk Cards (broad)
    "leakreplay",      # Information leakage — training data memorization
    "snowball",        # Safety alignment — escalating false claims
    # "knowledgebase" removed — not a valid probe module in garak ≤0.14.1
]


def seed_models(session) -> tuple[int, int]:
    """
    Insert free-tier models into the database, skipping existing ones.
    
    Returns:
        (inserted_count, skipped_count)
    """
    today = date.today()
    inserted = 0
    skipped = 0

    for name, provider, version in TEST_MODELS:
        existing = session.query(Model).filter(Model.name == name).first()
        if existing:
            skipped += 1
            continue

        model = Model(
            name=name,
            provider=provider,
            version=version,
            snapshot_date=today,
            is_active=True,
        )
        session.add(model)
        inserted += 1

    session.commit()
    return inserted, skipped


def seed_probe_queue(session) -> tuple[int, int]:
    """
    Insert ProbeRunQueue entries for each active model × each probe category.

    Skips entries that already exist with status "pending" or "running" to
    maintain idempotency.

    Returns:
        (inserted_count, skipped_count)
    """
    today = date.today()
    inserted = 0
    skipped = 0

    model_names = [name for name, _, _ in TEST_MODELS]
    models = session.query(Model).filter(Model.name.in_(model_names), Model.is_active == True).all()  # noqa: E712

    for model in models:
        for probe_category in PROBE_CATEGORIES:
            existing = (
                session.query(ProbeRunQueue)
                .filter(
                    ProbeRunQueue.model_id == model.id,
                    ProbeRunQueue.probe_category == probe_category,
                    ProbeRunQueue.status.in_(("pending", "running")),
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            entry = ProbeRunQueue(
                model_id=model.id,
                probe_category=probe_category,
                scheduled_date=today,
                status="pending",
            )
            session.add(entry)
            inserted += 1

    session.commit()
    return inserted, skipped


def dispatch_pending_queue(session) -> tuple[int, int]:
    """
    Read pending ProbeRunQueue entries for TEST_MODELS, create Run records,
    and dispatch them to Celery via publish_run_job().

    Skips models that already have a pending or running Run to avoid duplicate
    dispatches.

    Returns:
        (dispatched_model_count, dispatched_probe_count)
    """
    model_names = [name for name, _, _ in TEST_MODELS]
    models = (
        session.query(Model)
        .filter(Model.name.in_(model_names), Model.is_active == True)  # noqa: E712
        .all()
    )
    model_by_id = {model.id: model for model in models}

    pending_entries = (
        session.query(ProbeRunQueue)
        .filter(
            ProbeRunQueue.model_id.in_(list(model_by_id.keys())),
            ProbeRunQueue.status == "pending",
        )
        .all()
    )

    # Group queue entries by model_id
    entries_by_model: dict = defaultdict(list)
    for entry in pending_entries:
        entries_by_model[entry.model_id].append(entry)

    dispatched_models = 0
    dispatched_probes = 0

    for model_id, entries in entries_by_model.items():
        model = model_by_id[model_id]

        # Skip if the model already has an active (pending or running) Run
        active_run = (
            session.query(Run)
            .filter(
                Run.model_id == model_id,
                Run.status.in_(("pending", "running")),
            )
            .first()
        )
        if active_run:
            print(f"  ↷ Skipping {model.name} — already has an active run ({active_run.status})")
            continue

        categories = [entry.probe_category for entry in entries]

        # Create a new Run record
        run = Run(
            model_id=model_id,
            triggered_by="seed_script",
            status="pending",
        )
        session.add(run)
        session.flush()  # Populate run.id before using it

        # Dispatch to Celery; revert on connection failure
        try:
            publish_run_job(str(run.id), model.name, categories)
        except Exception as e:
            print(f"  ⚠ Failed to dispatch {model.name}: {e}")
            run.status = "failed"
            for entry in entries:
                entry.status = "pending"
            session.commit()
            continue

        # Mark queue entries as running
        for entry in entries:
            entry.status = "running"

        dispatched_models += 1
        dispatched_probes += len(categories)
        print(f"  ✓ Dispatched {model.name} — {len(categories)} probe categories")

    session.commit()
    return dispatched_models, dispatched_probes


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Seed GarakBoard models and probe queue")
    parser.add_argument("--dispatch", action="store_true",
                        help="Also dispatch pending queue entries to Celery workers")
    args = parser.parse_args()

    print("GarakBoard — Seeding OpenRouter free-tier models...")
    init_db()
    session = SessionLocal()
    try:
        inserted, skipped = seed_models(session)
        print(f"✓ Inserted: {inserted} models")
        print(f"↷ Skipped (already exist): {skipped} models")
        print(f"Total in catalogue: {len(TEST_MODELS)} models")

        print()
        print("GarakBoard — Seeding probe run queue...")
        queued, probe_skipped = seed_probe_queue(session)
        model_count = len(TEST_MODELS)
        total = len(PROBE_CATEGORIES) * model_count
        print(f"✓ Queued: {queued} probe runs")
        print(f"↷ Skipped (already queued): {probe_skipped} probe runs")
        print(
            f"Total coverage: {len(PROBE_CATEGORIES)} probes × {model_count} models"
            f" = {total} combinations"
        )

        if args.dispatch:
            print()
            print("Dispatching pending probe runs to Celery workers...")
            models_dispatched, probes_dispatched = dispatch_pending_queue(session)
            print(f"✓ Dispatched: {models_dispatched} models ({probes_dispatched} probe categories)")
            if models_dispatched == 0:
                print("  (All models already have active runs — nothing to dispatch)")
        else:
            pending = session.query(ProbeRunQueue).filter(
                ProbeRunQueue.status == "pending"
            ).count()
            if pending > 0:
                print(f"\n💡 {pending} probe runs are pending. Use --dispatch to send them to Celery workers.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
