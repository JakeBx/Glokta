#!/usr/bin/env python3
"""
Diagnose failed runs in the database.

Prints:
  1. Status overview (pending / running / complete / failed counts)
  2. Stale running runs (stuck > 2.5 hours)
  3. Failed runs broken down by model
  4. Error pattern distribution (parsed from raw_output)
  5. Detail for the 10 most recent failures

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/diagnose_failed_runs.py
"""

import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import func

from glokta.database import SessionLocal
from glokta.models import Model, Run

STALE_THRESHOLD = timedelta(hours=2, minutes=30)

ERROR_PATTERNS: list[tuple[str, list[str]]] = [
    ("TIMEOUT",        ["TimeoutExpired", "timed out", "timeout"]),
    ("AUTH_ERROR",     ["401", "AuthenticationError", "Unauthorized"]),
    ("QUOTA_EXCEEDED", ["403", "Forbidden", "quota"]),
    ("EMPTY_INGEST",   ["EmptyIngestError", "zero results", "zero probe results"]),
    ("MISSING_OUTPUT", ["FileNotFoundError", "No such file"]),
    ("GARAK_CRASH",    ["CalledProcessError", "non-zero exit"]),
    ("CONNECTIVITY",   ["ConnectionError", "ConnectionRefused", "Connection refused"]),
]


def classify(raw_output: str | None) -> str:
    if not raw_output:
        return "NO_OUTPUT"
    text = raw_output.lower()
    for label, keywords in ERROR_PATTERNS:
        if any(kw.lower() in text for kw in keywords):
            return label
    return "UNKNOWN"


def hr(char: str = "-", width: int = 70) -> None:
    print(char * width)


def section(title: str) -> None:
    print()
    hr("=")
    print(f"  {title}")
    hr("=")


def main() -> None:
    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # ── 1. Status overview ────────────────────────────────────────────────
        section("1. Status Overview")
        counts: dict[str, int] = {}
        for status in ("pending", "running", "complete", "failed"):
            counts[status] = session.query(func.count(Run.id)).filter(Run.status == status).scalar() or 0
        total = sum(counts.values())
        print(f"  {'Status':<12} {'Count':>7}  {'%':>6}")
        hr()
        for status, count in counts.items():
            pct = (count / total * 100) if total else 0
            print(f"  {status:<12} {count:>7}  {pct:>5.1f}%")
        hr()
        print(f"  {'TOTAL':<12} {total:>7}")

        # ── 2. Stale running runs ─────────────────────────────────────────────
        section("2. Stale Running Runs (started > 2.5 h ago)")
        cutoff = now - STALE_THRESHOLD
        # started_at may be timezone-naive in the DB — compare without tz if needed
        stale_runs = (
            session.query(Run)
            .filter(Run.status == "running", Run.started_at.isnot(None))
            .join(Model, Run.model_id == Model.id)
            .all()
        )
        stale = [r for r in stale_runs if _as_utc(r.started_at) < cutoff]
        if not stale:
            print("  None found.")
        else:
            print(f"  {'Run ID':<38} {'Model':<35} {'Started':<22} {'Elapsed'}")
            hr()
            for r in stale:
                elapsed = now - _as_utc(r.started_at)
                print(f"  {str(r.id):<38} {r.model.name:<35} {str(r.started_at):<22} {_fmt_delta(elapsed)}")

        # ── 3. Failed runs by model ───────────────────────────────────────────
        section("3. Failed Runs by Model")
        failed_runs = (
            session.query(Run)
            .filter(Run.status == "failed")
            .join(Model, Run.model_id == Model.id)
            .order_by(Run.completed_at.desc().nullslast())
            .all()
        )
        if not failed_runs:
            print("  No failed runs.")
        else:
            by_model: dict[str, list[Run]] = defaultdict(list)
            for r in failed_runs:
                by_model[r.model.name].append(r)
            rows = sorted(by_model.items(), key=lambda kv: len(kv[1]), reverse=True)
            print(f"  {'Model':<45} {'Count':>7}  {'Most Recent Failure'}")
            hr()
            for model_name, runs in rows:
                most_recent = runs[0].completed_at or runs[0].created_at
                print(f"  {model_name:<45} {len(runs):>7}  {most_recent}")

        # ── 4. Error pattern distribution ─────────────────────────────────────
        section("4. Error Pattern Distribution")
        category_counts: Counter[str] = Counter(classify(r.raw_output) for r in failed_runs)
        if not category_counts:
            print("  No failed runs to classify.")
        else:
            total_failed = len(failed_runs)
            print(f"  {'Category':<20} {'Count':>7}  {'%':>6}")
            hr()
            for cat, cnt in category_counts.most_common():
                pct = cnt / total_failed * 100
                print(f"  {cat:<20} {cnt:>7}  {pct:>5.1f}%")

        # ── 5. Recent failure detail ──────────────────────────────────────────
        section("5. Recent Failures (last 10)")
        recent = failed_runs[:10]
        if not recent:
            print("  No failed runs.")
        for r in recent:
            elapsed = None
            if r.started_at and r.completed_at:
                elapsed = _as_utc(r.completed_at) - _as_utc(r.started_at)
            category = classify(r.raw_output)
            print()
            print(f"  Run:        {r.id}")
            print(f"  Model:      {r.model.name}")
            print(f"  Triggered:  {r.triggered_by}")
            print(f"  Created:    {r.created_at}")
            print(f"  Started:    {r.started_at}")
            print(f"  Completed:  {r.completed_at}")
            print(f"  Elapsed:    {_fmt_delta(elapsed) if elapsed else 'n/a'}")
            print(f"  Category:   {category}")
            if r.raw_output:
                tail = r.raw_output[-500:].strip()
                print(f"  Output tail:")
                hr("-", 70)
                for line in tail.splitlines():
                    print(f"    {line}")
                hr("-", 70)
            else:
                print("  Output:     (none)")

        print()

    finally:
        session.close()


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_delta(delta: timedelta | None) -> str:
    if delta is None:
        return "n/a"
    total = int(delta.total_seconds())
    h, rem = divmod(abs(total), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


if __name__ == "__main__":
    main()
