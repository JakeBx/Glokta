#!/usr/bin/env python3
"""Queue training-mode garak scan runs for LLM01 data collection.

Creates two Run records per target model:
  1. Main run  — full TRAINING_PROBE_CATEGORIES, probe_prompt_cap=150, parallel=16, timeout=4h
  2. atkgen pass — atkgen probe only, probe_prompt_cap=50 (interactive overhead)

Usage:
    PYTHONPATH=src python scripts/run_training_data_scan.py [--model MODEL_ID] [--dry-run] [--overwrite]
    PYTHONPATH=src python scripts/run_training_data_scan.py --provider hf [--top-n 5] [--dry-run]

Run in priority order per data-requirements.md:
    python scripts/run_training_data_scan.py --model x-ai/grok-3-mini
    python scripts/run_training_data_scan.py --model x-ai/grok-3
    ...
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glokta.config import settings
from glokta.database import SessionLocal, init_db
from glokta.models import Model, Run
from glokta.worker.garak_runner import TRAINING_PROBE_CATEGORIES

TARGET_MODELS = [
    # "x-ai/grok-3-mini",
    # "x-ai/grok-3",
    # "deepseek/deepseek-r1",
    # "deepseek/deepseek-v3",
    "z-ai/glm-4.7-flash",
    "mistralai/voxtral-small-24b-2507",
    "cohere/command-a",
    "mistralai/devstral-2512",
]

_MAIN_PROMPT_CAP = 150
_ATKGEN_PROMPT_CAP = 50
_PARALLEL_ATTEMPTS = 16
_SCAN_TIMEOUT = 14400*3  # 12 hours


def _openrouter_name(model_id: str) -> str:
    return model_id if model_id.startswith("openrouter/") else f"openrouter/{model_id}"


def _hf_name(model_id: str) -> str:
    return model_id if model_id.startswith("huggingface/") else f"huggingface/{model_id}"


def _upsert_model(session, model_name: str) -> Model:
    existing = session.query(Model).filter(Model.name == model_name).first()
    if existing is not None:
        return existing
    provider = model_name.split("/", 2)[1] if model_name.count("/") >= 2 else model_name.split("/")[0]
    model = Model(
        name=model_name,
        provider=provider,
        snapshot_date=date.today(),
        is_active=True,
    )
    session.add(model)
    session.flush()
    return model


def _has_training_run(session, model_id, probe_categories_json: str) -> Run | None:
    return (
        session.query(Run)
        .filter(
            Run.model_id == model_id,
            Run.triggered_by == "training",
            Run.probe_categories_json == probe_categories_json,
            Run.status.in_(("pending", "running", "complete")),
        )
        .first()
    )


def queue_model(session, model_name: str, dry_run: bool, overwrite: bool) -> int:
    """Queue main + atkgen runs for one model. Returns number of runs queued."""
    model = _upsert_model(session, model_name)
    queued = 0

    main_cats_json = json.dumps(TRAINING_PROBE_CATEGORIES)
    atkgen_cats_json = json.dumps(["atkgen"])

    for label, cats_json, cap in [
        ("main", main_cats_json, _MAIN_PROMPT_CAP),
        ("atkgen", atkgen_cats_json, _ATKGEN_PROMPT_CAP),
    ]:
        if not overwrite:
            existing = _has_training_run(session, model.id, cats_json)
            if existing is not None:
                print(f"  ↷ Skipping {model_name} ({label}) — {existing.status} run already exists")
                continue

        if dry_run:
            cats = json.loads(cats_json)
            print(
                f"  [dry-run] Would queue {model_name} ({label})"
                f"  probes={cats}  cap={cap}  parallel={_PARALLEL_ATTEMPTS}  timeout={_SCAN_TIMEOUT}s"
            )
            queued += 1
            continue

        run = Run(
            model_id=model.id,
            triggered_by="training",
            status="pending",
            probe_categories_json=cats_json,
            probe_prompt_cap=cap,
            parallel_attempts_override=_PARALLEL_ATTEMPTS,
            scan_timeout_seconds=_SCAN_TIMEOUT,
        )
        session.add(run)
        session.commit()
        print(f"  ✓ Queued {model_name} ({label}) run {run.id}  cap={cap}")
        queued += 1

    return queued


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--model",
        help="Queue one specific model ID. For OpenRouter omit the prefix; for HF use --provider hf.",
    )
    parser.add_argument(
        "--provider",
        choices=["openrouter", "hf"],
        default="openrouter",
        help="Provider to target. 'hf' auto-discovers top warm inference models from HuggingFace.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of HF models to auto-discover when --provider hf is used (default: 5).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be queued without creating DB rows")
    parser.add_argument("--overwrite", action="store_true", help="Re-queue even if a training run already exists")
    args = parser.parse_args()

    if args.provider == "hf":
        from glokta.worker.hf_client import fetch_top_hf_models
        if not settings.hf_token:
            print("ERROR: HF_TOKEN is not set. Set it in .env or as an environment variable.")
            return 1
        if args.model:
            raw_ids = [args.model]
        else:
            print(f"Discovering top {args.top_n} HuggingFace warm inference models...")
            discovered = fetch_top_hf_models(hf_token=settings.hf_token, top_n=args.top_n)
            raw_ids = [m["id"] for m in discovered]
            print(f"Found: {raw_ids}")
        model_names = [_hf_name(rid) for rid in raw_ids]
    else:
        raw_ids = [args.model] if args.model else TARGET_MODELS
        model_names = [_openrouter_name(rid) for rid in raw_ids]

    init_db()
    session = SessionLocal()
    total_queued = 0
    try:
        for model_name in model_names:
            print(f"\n{model_name}:")
            total_queued += queue_model(session, model_name, args.dry_run, args.overwrite)
    finally:
        session.close()

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\nTotal runs queued{suffix}: {total_queued}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
