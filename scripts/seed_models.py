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

# Current OpenRouter free-tier model catalogue (as of April 2026)
# Format: (name, provider, version)
# All names use the openrouter/<provider>/<model>:free convention
FREE_TIER_MODELS = [
    ("openrouter/meta-llama/llama-3.1-8b-instruct:free", "meta-llama", "3.1-8b-instruct"),
    ("openrouter/meta-llama/llama-3.2-3b-instruct:free", "meta-llama", "3.2-3b-instruct"),
    ("openrouter/meta-llama/llama-3.2-1b-instruct:free", "meta-llama", "3.2-1b-instruct"),
    ("openrouter/meta-llama/llama-3.3-70b-instruct:free", "meta-llama", "3.3-70b-instruct"),
    ("openrouter/mistralai/mistral-7b-instruct:free", "mistralai", "7b-instruct"),
    ("openrouter/mistralai/mistral-nemo:free", "mistralai", "nemo"),
    ("openrouter/google/gemma-2-9b-it:free", "google", "gemma-2-9b-it"),
    ("openrouter/google/gemma-2-27b-it:free", "google", "gemma-2-27b-it"),
    ("openrouter/google/gemma-3-1b-it:free", "google", "gemma-3-1b-it"),
    ("openrouter/google/gemma-3-4b-it:free", "google", "gemma-3-4b-it"),
    ("openrouter/google/gemma-3-12b-it:free", "google", "gemma-3-12b-it"),
    ("openrouter/google/gemma-3-27b-it:free", "google", "gemma-3-27b-it"),
    ("openrouter/microsoft/phi-3-mini-128k-instruct:free", "microsoft", "phi-3-mini-128k"),
    ("openrouter/microsoft/phi-3-medium-128k-instruct:free", "microsoft", "phi-3-medium-128k"),
    ("openrouter/qwen/qwen-2.5-7b-instruct:free", "qwen", "2.5-7b-instruct"),
    ("openrouter/qwen/qwen-2.5-72b-instruct:free", "qwen", "2.5-72b-instruct"),
    ("openrouter/qwen/qwen3-8b:free", "qwen", "3-8b"),
    ("openrouter/qwen/qwen3-14b:free", "qwen", "3-14b"),
    ("openrouter/qwen/qwen3-30b-a3b:free", "qwen", "3-30b-a3b"),
    ("openrouter/qwen/qwen3-32b:free", "qwen", "3-32b"),
    ("openrouter/deepseek/deepseek-r1:free", "deepseek", "r1"),
    ("openrouter/deepseek/deepseek-r1-zero:free", "deepseek", "r1-zero"),
    ("openrouter/deepseek/deepseek-v3-base:free", "deepseek", "v3-base"),
    ("openrouter/nvidia/llama-3.1-nemotron-70b-instruct:free", "nvidia", "llama-3.1-nemotron-70b"),
    ("openrouter/nousresearch/hermes-3-llama-3.1-405b:free", "nousresearch", "hermes-3-llama-3.1-405b"),
    ("openrouter/openchat/openchat-7b:free", "openchat", "7b"),
    ("openrouter/gryphe/mythomist-7b:free", "gryphe", "mythomist-7b"),
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

    for name, provider, version in FREE_TIER_MODELS:
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
        print(f"Total in catalogue: {len(FREE_TIER_MODELS)} models")
    finally:
        session.close()


if __name__ == "__main__":
    main()