#!/usr/bin/env python3
"""
Seed the GarakBoard database with OpenRouter free-tier models.

Usage (conda dev env):
    PYTHONPATH=src conda run -n garakboard python scripts/seed_models.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/seed_models.py

The script is idempotent: models that already exist (by name) are skipped.
"""

import sys
import os
from datetime import date

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garakboard.database import SessionLocal, init_db
from garakboard.models import Model

# Current OpenRouter free-tier model catalogue (verified April 2026)
# Format: (name, provider, version)
# All names use the openrouter/<provider>/<model>:free convention
TEST_MODELS = [
    ("openrouter/minimax/minimax-m2.5", "minimax", "m2.5"),
    ("openrouter/google/gemini-2.5-flash-lite", "google", "gemini-2.5-flash-lite"),
    ("openrouter/openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
    ("openrouter/mistralai/mistral-nemo", "mistralai", "nemo"),
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


def main():
    print("GarakBoard — Seeding OpenRouter free-tier models...")
    init_db()
    session = SessionLocal()
    try:
        inserted, skipped = seed_models(session)
        print(f"✓ Inserted: {inserted} models")
        print(f"↷ Skipped (already exist): {skipped} models")
        print(f"Total in catalogue: {len(TEST_MODELS)} models")
    finally:
        session.close()


if __name__ == "__main__":
    main()