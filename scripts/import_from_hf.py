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

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.hf_sync import import_all


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a Glokta HuggingFace dataset into the local DB (idempotent merge)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and report what would be imported without writing to the DB",
    )
    args = parser.parse_args()

    if not settings.hf_dataset_repo:
        print("✗ HF_DATASET_REPO is not set. Add it to your .env file or export it as an environment variable.")
        sys.exit(1)

    print("Glokta — Importing HuggingFace dataset into database...")
    print(f"  Source repo: {settings.hf_dataset_repo}")
    if args.dry_run:
        print("  Mode: dry-run (no DB writes)")
    print()

    try:
        import_all(dry_run=args.dry_run)
    except RuntimeError as e:
        print(f"✗ {e}")
        sys.exit(1)

    if args.dry_run:
        print("✓ Dry-run complete — no data was written to the database.")
    else:
        print("✓ Import complete.")


if __name__ == "__main__":
    main()
