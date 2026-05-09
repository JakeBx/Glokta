"""Assess hit counts and readiness of training data collected from garak runs.

Usage:
    PYTHONPATH=src python scripts/assess_garak_dataset.py [--triggered-by training] [--min-hits 3000]

Exit codes:
    0 — target reached (>= min_hits deduplicated hits)
    1 — below target but not critical (>= 100 hits)
    2 — critical (< 100 hits)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Pure functions — testable without a DB connection
# ---------------------------------------------------------------------------


def readiness_label(hit_count: int) -> str:
    if hit_count >= 3000:
        return "TARGET REACHED — proceed to export"
    if hit_count >= 500:
        return "SUFFICIENT for validation SFT"
    if hit_count >= 100:
        return "MARGINAL — sufficient for validation SFT only"
    return "CRITICAL — dataset too small, do not train"


def readiness_exit_code(hit_count: int, min_hits: int = 3000) -> int:
    if hit_count < 100:
        return 2
    if hit_count < min_hits:
        return 1
    return 0


def hit_rate_label(hit_rate_pct: float) -> str:
    if hit_rate_pct >= 15.0:
        return "PASS"
    if hit_rate_pct >= 10.0:
        return "WARN"
    return "FAIL"


def deduplicate_prompts(prompts: list[str]) -> int:
    return len({p.strip() for p in prompts})


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


def _is_hit(detector_outcome: dict | None) -> bool:
    return bool(detector_outcome) and any(detector_outcome.values())


def _run_assessment(triggered_by: str, min_hits: int) -> int:
    from garakboard.database import SessionLocal, init_db
    from garakboard.models import Run
    from garakboard.models.attempt import Attempt
    from garakboard.models.model import Model

    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Attempt.prompt,
                Attempt.response,
                Attempt.probe_name,
                Attempt.detector_outcome,
                Model.name.label("model_name"),
            )
            .join(Run, Attempt.run_id == Run.id)
            .join(Model, Run.model_id == Model.id)
            .filter(Run.triggered_by == triggered_by, Run.status == "complete")
            .all()
        )
    finally:
        db.close()

    total_attempts = len(rows)
    hits = [r for r in rows if _is_hit(r.detector_outcome)]
    total_hits = len(hits)

    all_prompts = [r.prompt or "" for r in rows]
    hit_prompts = [r.prompt or "" for r in hits]
    dedup_hits = deduplicate_prompts(hit_prompts)

    null_count = sum(1 for r in rows if not r.response or not r.response.strip())
    null_rate = (null_count / total_attempts * 100) if total_attempts else 0.0
    hit_rate = (total_hits / total_attempts * 100) if total_attempts else 0.0

    # Aggregate by probe family
    from collections import defaultdict
    by_family: dict[str, int] = defaultdict(int)
    by_model: dict[str, int] = defaultdict(int)
    for r in hits:
        family = r.probe_name.split(".")[0] if r.probe_name else "unknown"
        by_family[family] += 1
        by_model[r.model_name] += 1

    print("=== GarakBoard Training Dataset Assessment ===")
    print(f"Total training run attempts:  {total_attempts:,}")
    print(f"Total hits (compliant):       {total_hits:,}")
    print(f"Deduplicated hits:            {dedup_hits:,}")
    print(f"Null/empty response rate:     {null_rate:.1f}%")
    print()

    hr_label = hit_rate_label(hit_rate)
    print(f"Hit rate: {hit_rate:.1f}%  [{hr_label}]")
    print()

    print("By probe family:")
    for family, count in sorted(by_family.items(), key=lambda x: -x[1]):
        print(f"  {family:<22} {count:,} hits")
    families_with_hits = len(by_family)
    family_status = "PASS" if families_with_hits >= 6 else "FAIL"
    print(f"  Families with hits: {families_with_hits}  [{family_status} — need >= 6]")
    print()

    print("By source model:")
    for model_name, count in sorted(by_model.items(), key=lambda x: -x[1]):
        print(f"  {model_name:<40} {count:,} hits")
    models_with_hits = len(by_model)
    model_status = "PASS" if models_with_hits >= 3 else "FAIL"
    print(f"  Models with hits: {models_with_hits}  [{model_status} — need >= 3]")
    print()

    label = readiness_label(dedup_hits)
    print(f"READINESS: {label}")

    return readiness_exit_code(dedup_hits, min_hits)


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess garak training dataset readiness")
    parser.add_argument("--triggered-by", default="training", help="Run trigger label to filter on")
    parser.add_argument("--min-hits", type=int, default=3000, help="Deduplicated hit target")
    args = parser.parse_args()
    return _run_assessment(args.triggered_by, args.min_hits)


if __name__ == "__main__":
    sys.exit(main())
