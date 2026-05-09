#!/usr/bin/env python3
"""Export training data from completed garak runs as an SFT dataset.

Queries training run attempts, applies quality filters, deduplicates on prompt,
splits 90/10 train/eval, and pushes to HuggingFace (or writes local parquet).

Usage:
    PYTHONPATH=src python scripts/export_sft_dataset.py [--dry-run] [--hf-repo REPO] [--output-dir PATH]
    PYTHONPATH=src python scripts/export_sft_dataset.py --include-static --output-dir ./sft_output
"""

import argparse
import dataclasses
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garakboard.data.filters import SFTRecord, apply_quality_filters


def _load_garak_records(triggered_by: str) -> list[SFTRecord]:
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

    records = []
    for r in rows:
        probe_name = r.probe_name or "unknown.unknown"
        category = probe_name.split(".")[0]
        records.append(SFTRecord(
            prompt=r.prompt or "",
            response=r.response,
            owasp_id="LLM01",
            vulnerability="V1",
            source_model=r.model_name,
            probe_name=probe_name,
            probe_category=category,
            source="garak",
            detector_outcome=r.detector_outcome,
        ))
    return records


def _dedup_records(records: list[SFTRecord]) -> list[SFTRecord]:
    seen: set[str] = set()
    result = []
    for r in records:
        key = r.prompt.strip()
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


def _split_records(records: list[SFTRecord], seed: int = 42) -> tuple[list[SFTRecord], list[SFTRecord]]:
    shuffled = records.copy()
    random.seed(seed)
    random.shuffle(shuffled)
    split = int(len(shuffled) * 0.9)
    return shuffled[:split], shuffled[split:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--triggered-by", default="training", help="Run trigger label to query (default: training)")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only, do not export")
    parser.add_argument("--hf-repo", help="HuggingFace dataset repo to push to (overrides HF_DATASET_REPO env var)")
    parser.add_argument("--output-dir", help="Write parquet files locally instead of pushing to HF")
    args = parser.parse_args()

    records = _load_garak_records(args.triggered_by)
    print(f"Loaded {len(records):,} raw garak records")

    filtered = apply_quality_filters(records)
    print(f"After quality filters: {len(filtered):,}")

    deduped = _dedup_records(filtered)
    print(f"After dedup: {len(deduped):,}")

    train, eval_ = _split_records(deduped)
    print(f"Train: {len(train):,}  Eval: {len(eval_):,}")

    if args.dry_run:
        print("[dry-run] No export performed.")
        return 0

    dicts_train = [dataclasses.asdict(r) for r in train]
    dicts_eval = [dataclasses.asdict(r) for r in eval_]

    if args.output_dir:
        import pathlib
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            print("pyarrow not installed. Run: pip install pyarrow", file=sys.stderr)
            return 1
        out = pathlib.Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(dicts_train), out / "train.parquet")
        pq.write_table(pa.Table.from_pylist(dicts_eval), out / "eval.parquet")
        print(f"Written to {args.output_dir}/")
        return 0

    hf_repo = args.hf_repo or os.environ.get("HF_DATASET_REPO")
    if not hf_repo:
        print("Error: set --hf-repo or HF_DATASET_REPO env var", file=sys.stderr)
        return 1

    try:
        from datasets import Dataset, DatasetDict
    except ImportError:
        print("datasets not installed. Add the [dataset] extra.", file=sys.stderr)
        return 1

    ds = DatasetDict({
        "train": Dataset.from_list(dicts_train),
        "eval": Dataset.from_list(dicts_eval),
    })
    hf_token = os.environ.get("HF_TOKEN")
    ds.push_to_hub(hf_repo, token=hf_token)
    print(f"Pushed to {hf_repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
