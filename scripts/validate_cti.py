#!/usr/bin/env python3
"""Validate CTI benchmark DB outputs after a smoke run.

Checks, per task, that the run completed and produced scored results with the expected fields
(score, pre/post-cutoff tag, prompt hash), that Forecast recorded a run-level AUC, and that the
model's cutoff is populated. Prints a per-task report and exits non-zero on any failure.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/validate_cti.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import glokta.infrastructure.db.orm  # noqa: F401
from glokta.infrastructure.db.orm import CtiResult, CtiRun, Model
from glokta.infrastructure.db.session import SessionLocal

MODEL_NAME = "huggingface/meta-llama/Llama-3.1-8B-Instruct"
TASKS = ["rcm", "vsp", "forecast", "ate", "taa", "syn"]


def main() -> None:
    db = SessionLocal()
    failures: list[str] = []
    try:
        model = db.query(Model).filter(Model.name == MODEL_NAME).first()
        if model is None:
            print("FAIL: model not found")
            sys.exit(1)
        print(f"model: {MODEL_NAME}")
        print(f"cutoff range: {model.cutoff_start} .. {model.cutoff_end}")
        if model.cutoff_end is None:
            failures.append("model cutoff not populated")
        print("-" * 92)
        print(f"{'task':9} {'run':9} {'items':5} {'scored':6} {'prequential':12} {'extra':20} sample")
        print("-" * 92)

        for task in TASKS:
            run = (
                db.query(CtiRun)
                .filter(CtiRun.model_id == model.id, CtiRun.task == task)
                .order_by(CtiRun.created_at.desc())
                .first()
            )
            if run is None:
                failures.append(f"{task}: no run")
                print(f"{task:9} MISSING")
                continue

            results = db.query(CtiResult).filter(CtiResult.run_id == run.id).all()
            scored = [r for r in results if r.score is not None]
            preq = f"{run.prequential_score:.3f}" if run.prequential_score is not None else "None"

            extra = ""
            if task == "forecast":
                auc = (run.config or {}).get("auc")
                extra = f"auc={auc}"
                if "auc" not in (run.config or {}):
                    failures.append(f"{task}: missing run-level auc")

            sample = ""
            if scored:
                r = scored[0]
                pre = "pre" if r.pre_cutoff else "post"
                sample = f"score={r.score:.3f} {pre}cutoff correct={r.correct} out={str(r.parsed_output)[:40]}"
            else:
                failures.append(f"{task}: no scored results")

            # per-task field checks
            for r in scored:
                if r.pre_cutoff is None:
                    failures.append(f"{task}: result missing pre_cutoff tag")
                if not r.prompt_hash:
                    failures.append(f"{task}: result missing prompt_hash")
                break

            status = run.status
            if status != "complete":
                failures.append(f"{task}: run status {status}")

            print(f"{task:9} {status:9} {run.item_count:<5} {len(scored):<6} {preq:12} {extra:20} {sample}")

        print("-" * 92)
        if failures:
            print(f"VALIDATION FAILED ({len(failures)} issue(s)):")
            for f in failures:
                print(f"  - {f}")
            sys.exit(1)
        print("VALIDATION PASSED: all six benchmarks produced scored DB outputs.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
