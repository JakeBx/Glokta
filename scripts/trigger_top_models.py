#!/usr/bin/env python3
"""
Manually trigger scan runs for the top OpenRouter models under a USD cost cap.

Usage (conda dev env):
    PYTHONPATH=src conda run -n glokta python scripts/trigger_top_models.py

Usage (Docker):
    docker compose -f docker/docker-compose.yml exec api python /app/scripts/trigger_top_models.py

Defaults to the top 20 models below a $5/scan cap. Override via flags:
    --top-n 10 --max-cost 2.50
    --dry-run                  # show selection only

Runs are created with status='pending'. The Prefect pipeline picks them up on its
next 2-minute poll cycle (scan-pending-runs flow).
"""

import argparse
import os
import sys
from datetime import date

# Allow running from repo root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.database import SessionLocal, init_db
from glokta.models import Model, Run
from glokta.worker.garak_runner import DEFAULT_PROBE_CATEGORIES
from glokta.worker.openrouter_client import estimate_scan_cost_usd, fetch_top_models


def _openrouter_name(model_id: str) -> str:
    """Prefix the catalog id with 'openrouter/' so LiteLLM routes through OpenRouter.

    Without this prefix LiteLLM treats e.g. 'minimax/minimax-m2.5' as a native
    Minimax model and dispatches to api.minimax.io (which rejects our key).
    """
    return model_id if model_id.startswith("openrouter/") else f"openrouter/{model_id}"


def _upsert_model(session, model_name: str) -> Model:
    """Return existing Model by name or create one using the provider prefix."""
    existing = session.query(Model).filter(Model.name == model_name).first()
    if existing is not None:
        return existing

    provider = model_name.split("/", 1)[0] if "/" in model_name else "unknown"
    model = Model(
        name=model_name,
        provider=provider,
        snapshot_date=date.today(),
        is_active=True,
    )
    session.add(model)
    session.flush()
    return model


def trigger_scans(top_n: int, max_cost_usd: float, dry_run: bool, overwrite: bool = False) -> int:
    """Fetch top models under the cap and create pending Run records.

    The Prefect pipeline (scan-pending-runs flow, ~2 min interval) picks up the
    pending runs automatically.  Models that already have a pending, running, or
    complete run are skipped unless ``overwrite=True`` is passed explicitly.
    """
    models = fetch_top_models(
        api_key=settings.openrouter_api_key,
        top_n=top_n,
        max_scan_cost_usd=max_cost_usd,
    )

    print(f"Selected {len(models)} models (top {top_n} under ${max_cost_usd:.2f}/scan cap):")
    print(f"{'':>3}  {'Model':<60}  {'Est. scan cost':>14}")
    print("-" * 85)
    total = 0.0
    for i, m in enumerate(models, 1):
        cost = estimate_scan_cost_usd(m.get("pricing", {}))
        total += cost
        disp = f"${cost:.3f}" if cost > 0 else "free"
        name = _openrouter_name(m["id"])
        print(f"{i:>3}. {name:<60}  {disp:>14}")
    print("-" * 85)
    print(f"Total estimated cost if all runs complete: ${total:.2f}")
    print(f"Probe categories per run: {', '.join(DEFAULT_PROBE_CATEGORIES)}")

    if dry_run:
        print("\n[dry-run] No runs queued.")
        return 0

    if not models:
        return 0

    print()
    init_db()
    session = SessionLocal()
    queued = 0
    skipped = 0
    try:
        for m in models:
            model_name = _openrouter_name(m["id"])
            model = _upsert_model(session, model_name)

            if not overwrite:
                existing = (
                    session.query(Run)
                    .filter(
                        Run.model_id == model.id,
                        Run.status.in_(("pending", "running", "complete")),
                    )
                    .first()
                )
                if existing is not None:
                    print(f"  ↷ Skipping {model_name} — {existing.status} run {existing.id} already exists")
                    skipped += 1
                    continue

            run = Run(
                model_id=model.id,
                triggered_by="manual",
                status="pending",
            )
            session.add(run)
            session.commit()  # Prefect pipeline picks up pending runs on next poll

            queued += 1
            print(f"  ✓ Queued run {run.id} for {model_name}")
    finally:
        session.close()

    print(f"\nQueued {queued}/{len(models)} runs ({skipped} skipped — already active/complete).")
    if skipped:
        print("Use --overwrite to re-queue models with existing runs.")
    print("The Prefect pipeline will pick up pending runs on its next 2-minute poll cycle.")
    return queued


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top-n", type=int, default=20, help="Number of models to select (default: 20)")
    parser.add_argument("--max-cost", type=float, default=5.0, help="Per-model USD cost cap (default: 5.00)")
    parser.add_argument("--dry-run", action="store_true", help="Show selection without queueing runs")
    parser.add_argument("--overwrite", action="store_true", help="Re-queue models that already have active/complete runs")
    args = parser.parse_args()

    try:
        count = trigger_scans(args.top_n, args.max_cost, args.dry_run, args.overwrite)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if (count > 0 or args.dry_run) else 2)


if __name__ == "__main__":
    main()
