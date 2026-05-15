#!/usr/bin/env python3
"""
One-off cleanup for runs and models affected by the missing-`openrouter/` prefix
bug and the silent 0-result "complete" bug.

Actions:
  1. Re-classify any Run with status='complete' but zero probe_results AND zero
     attempts as status='failed'. These are runs where garak exited 0 but the
     generator silently failed (LiteLLM routing 401s, etc).
  2. For Models whose name lacks the 'openrouter/' prefix:
     - If a prefixed twin already exists, reassign the unprefixed Model's runs
       onto the prefixed Model, then delete the unprefixed row.
     - Otherwise, rename the Model in place.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/cleanup_bad_runs.py [--dry-run]
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.database import SessionLocal
from glokta.models import Attempt, Model, ProbeResult, Run


def reclassify_empty_complete_runs(session, dry_run: bool) -> int:
    """Mark complete-but-empty runs as failed."""
    candidates = (
        session.query(Run)
        .filter(Run.status == "complete")
        .all()
    )
    bad = []
    for run in candidates:
        pr_count = session.query(ProbeResult).filter(ProbeResult.run_id == run.id).count()
        att_count = session.query(Attempt).filter(Attempt.run_id == run.id).count()
        if pr_count == 0 and att_count == 0:
            bad.append(run)

    print(f"Found {len(bad)} empty 'complete' runs to reclassify as failed.")
    for run in bad:
        print(f"  - {run.id}  started={run.started_at}")
        if not dry_run:
            run.status = "failed"
            if run.completed_at is None:
                run.completed_at = datetime.now(timezone.utc)

    if not dry_run:
        session.commit()
    return len(bad)


def fix_model_prefixes(session, dry_run: bool) -> tuple[int, int]:
    """Prefix Model.name with 'openrouter/', merging collisions by reassigning runs."""
    unprefixed = session.query(Model).filter(~Model.name.startswith("openrouter/")).all()
    print(f"\nFound {len(unprefixed)} Models missing 'openrouter/' prefix.")

    renamed = 0
    merged = 0

    for model in unprefixed:
        target_name = f"openrouter/{model.name}"
        twin = session.query(Model).filter(Model.name == target_name).first()

        if twin and twin.id != model.id:
            # Merge: reassign all runs from model -> twin, then delete model
            run_count = session.query(Run).filter(Run.model_id == model.id).count()
            print(f"  MERGE  {model.name}  ->  {target_name}  ({run_count} runs moved)")
            if not dry_run:
                session.query(Run).filter(Run.model_id == model.id).update(
                    {Run.model_id: twin.id}
                )
                session.delete(model)
            merged += 1
        else:
            print(f"  RENAME {model.name}  ->  {target_name}")
            if not dry_run:
                model.name = target_name
            renamed += 1

    if not dry_run:
        session.commit()
    return renamed, merged


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        failed = reclassify_empty_complete_runs(session, args.dry_run)
        renamed, merged = fix_model_prefixes(session, args.dry_run)

        print()
        print(f"Summary: {failed} runs reclassified; {renamed} models renamed; {merged} models merged.")
        if args.dry_run:
            print("(dry-run — no changes committed)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
