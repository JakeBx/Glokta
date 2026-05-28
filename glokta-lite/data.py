"""
garkboard-lite data layer.

Loads models, runs, and probe_results from a HuggingFace Dataset into
in-memory pandas DataFrames.  All query functions operate on these DataFrames
directly — no HTTP API, no database.

Set HF_DATASET_REPO (default: Jake/glokta-public) and optionally HF_TOKEN
for private repos.
"""

import os
from datetime import datetime

import pandas as pd

from risks import ACTIVE_RISKS, compute_risk_pass_rates

HF_DATASET_REPO: str = os.environ.get("HF_DATASET_REPO", "Jake/glokta-public")
HF_TOKEN: str | None = os.environ.get("HF_TOKEN") or None

# Module-level cache — populated by load_data()
_models: pd.DataFrame = pd.DataFrame()
_runs: pd.DataFrame = pd.DataFrame()
_probe_results: pd.DataFrame = pd.DataFrame()


def load_data() -> None:
    """Load all three tables from the HF Dataset into memory.

    Safe to call multiple times (re-loads on each call).
    Raises RuntimeError if the dataset cannot be reached.
    """
    global _models, _runs, _probe_results

    try:
        from datasets import load_dataset
        models_ds = load_dataset(HF_DATASET_REPO, name="models", token=HF_TOKEN)
        runs_ds = load_dataset(HF_DATASET_REPO, name="runs", token=HF_TOKEN)
        probe_results_ds = load_dataset(HF_DATASET_REPO, name="probe_results", token=HF_TOKEN)
    except Exception as e:
        raise RuntimeError(f"Failed to load HF dataset '{HF_DATASET_REPO}': {e}") from e

    _models = models_ds["train"].to_pandas()
    _runs = runs_ds["train"].to_pandas()
    _probe_results = probe_results_ds["train"].to_pandas()

    # Normalise types
    for col in ("created_at", "started_at", "completed_at", "scanned_at"):
        if col in _runs.columns:
            _runs[col] = pd.to_datetime(_runs[col], utc=True, errors="coerce")

    if "created_at" in _models.columns:
        _models["created_at"] = pd.to_datetime(_models["created_at"], utc=True, errors="coerce")

    print(
        f"[data] Loaded {len(_models)} models, {len(_runs)} runs, "
        f"{len(_probe_results)} probe_results from {HF_DATASET_REPO}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_run_ids() -> dict[str, str]:
    """Return {model_id: run_id} for each model's most recent complete run."""
    complete = _runs[_runs["status"] == "complete"].copy()
    if complete.empty:
        return {}
    idx = complete.groupby("model_id")["completed_at"].idxmax()
    latest = complete.loc[idx, ["model_id", "id"]]
    return dict(zip(latest["model_id"], latest["id"]))


def _model_name(model_id: str) -> str:
    rows = _models[_models["id"] == model_id]
    return rows.iloc[0]["name"] if not rows.empty else model_id


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def get_models() -> list[dict]:
    """Return all models as a list of dicts."""
    if _models.empty:
        return []
    return _models[["id", "name", "provider"]].to_dict(orient="records")


def get_probe_categories() -> list[str]:
    """Return all unique probe categories present in probe_results."""
    if _probe_results.empty:
        return []
    return sorted(_probe_results["probe_category"].dropna().unique().tolist())


def get_leaderboard(
    probe_category: str | None = None,
    model_id: str | None = None,
) -> list[dict]:
    """Aggregate probe results from each model's latest complete run.

    Mirrors GET /api/leaderboard response shape:
      model_id, model_name, provider, probe_category,
      total_pass, total_fail, score, pass_rate, origin
    """
    if _probe_results.empty or _runs.empty or _models.empty:
        return []

    latest = _latest_run_ids()  # {model_id: run_id}
    if not latest:
        return []

    latest_run_ids = set(latest.values())
    pr = _probe_results[_probe_results["run_id"].isin(latest_run_ids)].copy()

    if probe_category:
        pr = pr[pr["probe_category"] == probe_category]
    if model_id:
        run_id = latest.get(model_id)
        if not run_id:
            return []
        pr = pr[pr["run_id"] == run_id]

    if pr.empty:
        return []

    # Join run.model_id back onto probe_results via run_id.
    # Exclude triggered_by from the join/groupby — it can be null and would silently
    # drop rows from the groupby (pandas default dropna=True behaviour).
    run_map = _runs[_runs["id"].isin(latest_run_ids)][["id", "model_id"]].copy()
    run_map = run_map.rename(columns={"id": "run_id"})
    pr = pr.merge(run_map, on="run_id", how="left")

    model_map = _models[["id", "name", "provider"]].rename(columns={"id": "model_id"})
    pr = pr.merge(model_map, on="model_id", how="left")

    grouped = (
        pr.groupby(["model_id", "name", "provider", "probe_category"])
        .agg(total_pass=("pass_count", "sum"), total_fail=("fail_count", "sum"), score=("score", "mean"))
        .reset_index()
    )
    grouped["pass_rate"] = grouped.apply(
        lambda r: r["total_pass"] / (r["total_pass"] + r["total_fail"])
        if (r["total_pass"] + r["total_fail"]) > 0 else 0.0,
        axis=1,
    )
    grouped = grouped.sort_values("score")

    # Look up triggered_by per model from its latest run separately to avoid groupby null drop.
    model_origin = {}
    for mid, rid in latest.items():
        row = _runs[_runs["id"] == rid]["triggered_by"]
        model_origin[mid] = row.iloc[0] if not row.empty and pd.notna(row.iloc[0]) else "api"

    return [
        {
            "model_id": row["model_id"],
            "model_name": row["name"],
            "provider": row["provider"],
            "probe_category": row["probe_category"],
            "total_pass": int(row["total_pass"]),
            "total_fail": int(row["total_fail"]),
            "score": float(row["score"]) if pd.notna(row["score"]) else 0.0,
            "pass_rate": float(row["pass_rate"]),
            "origin": model_origin.get(row["model_id"], "api"),
        }
        for _, row in grouped.iterrows()
    ]


def get_model_detail(model_id: str) -> dict | None:
    """Return probe results for a model's latest complete run."""
    latest = _latest_run_ids()
    run_id = latest.get(model_id)
    if not run_id:
        return None

    pr = _probe_results[_probe_results["run_id"] == run_id]
    if pr.empty:
        return None

    model_row = _models[_models["id"] == model_id]
    model_name = model_row.iloc[0]["name"] if not model_row.empty else model_id
    provider = model_row.iloc[0]["provider"] if not model_row.empty else ""

    probe_results = [
        {
            "probe_name": r["probe_name"],
            "probe_category": r["probe_category"],
            "detector": r["detector"],
            "pass_count": int(r["pass_count"]),
            "fail_count": int(r["fail_count"]),
            "score": float(r["score"]) if pd.notna(r.get("score")) else None,
        }
        for _, r in pr.iterrows()
    ]

    return {
        "model_id": model_id,
        "model_name": model_name,
        "provider": provider,
        "run_id": run_id,
        "probe_results": probe_results,
    }


def get_risk_leaderboard(included_risks: list[str]) -> list[dict]:
    """Risk-based leaderboard: mean of per-risk pass rates across included categories."""
    if _probe_results.empty or _models.empty:
        return []

    latest = _latest_run_ids()
    if not latest:
        return []

    results = []
    for model_id, run_id in latest.items():
        pr = _probe_results[_probe_results["run_id"] == run_id]
        pr_dicts = [
            {"probe_category": r["probe_category"],
             "pass_count": int(r["pass_count"]),
             "fail_count": int(r["fail_count"])}
            for _, r in pr.iterrows()
        ]
        per_risk, overall = compute_risk_pass_rates(pr_dicts, included_risks=included_risks)
        model_row = _models[_models["id"] == model_id]
        results.append({
            "model_id": model_id,
            "model_name": model_row.iloc[0]["name"] if not model_row.empty else model_id,
            "provider": model_row.iloc[0]["provider"] if not model_row.empty else "",
            "overall_pass_rate": overall,
            "per_risk": per_risk,
        })

    results.sort(key=lambda r: r["overall_pass_rate"] if r["overall_pass_rate"] is not None else -1, reverse=True)
    return results


def get_trends(model_id: str, included_risks: list[str]) -> dict | None:
    """All completed scan results for a model, ordered by date."""
    model_row = _models[_models["id"] == model_id]
    if model_row.empty:
        return None

    model_name = model_row.iloc[0]["name"]
    runs = _runs[(_runs["model_id"] == model_id) & (_runs["status"] == "complete")].copy()
    runs = runs.sort_values("completed_at")

    points = []
    for _, run in runs.iterrows():
        pr = _probe_results[_probe_results["run_id"] == run["id"]]
        pr_dicts = [
            {"probe_category": r["probe_category"],
             "pass_count": int(r["pass_count"]),
             "fail_count": int(r["fail_count"])}
            for _, r in pr.iterrows()
        ]
        per_risk, overall = compute_risk_pass_rates(pr_dicts, included_risks=included_risks)
        completed_at = run["completed_at"]
        if hasattr(completed_at, "isoformat"):
            completed_at = completed_at.isoformat()
        points.append({
            "run_id": run["id"],
            "completed_at": completed_at,
            "per_risk": per_risk,
            "overall_pass_rate": overall,
        })

    return {"model_id": model_id, "model_name": model_name, "points": points}


def get_run_summary() -> list[dict]:
    """Per-model run status counts."""
    if _runs.empty or _models.empty:
        return []

    model_map = dict(zip(_models["id"], _models["name"]))
    provider_map = dict(zip(_models["id"], _models["provider"]))

    summary = (
        _runs.groupby(["model_id", "status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for col in ("complete", "running", "pending", "failed"):
        if col not in summary.columns:
            summary[col] = 0

    # Latest origin per model
    latest_origin = (
        _runs[_runs["status"] == "complete"]
        .sort_values("completed_at")
        .groupby("model_id")["triggered_by"]
        .last()
    )

    rows = []
    for _, row in summary.iterrows():
        mid = row["model_id"]
        rows.append({
            "model_name": model_map.get(mid, mid),
            "provider": provider_map.get(mid, ""),
            "complete": int(row.get("complete", 0)),
            "running": int(row.get("running", 0)),
            "pending": int(row.get("pending", 0)),
            "failed": int(row.get("failed", 0)),
            "latest_origin": latest_origin.get(mid, ""),
        })
    return rows


def get_runs(status: str | None = None) -> list[dict]:
    """Recent runs (up to 200), optionally filtered by status."""
    if _runs.empty or _models.empty:
        return []

    model_map = dict(zip(_models["id"], _models["name"]))
    runs = _runs.copy()
    if status and status != "All":
        runs = runs[runs["status"] == status]

    runs = runs.sort_values("created_at", ascending=False).head(200)

    rows = []
    for _, r in runs.iterrows():
        rows.append({
            "id": r["id"],
            "model_id": r["model_id"],
            "model_name": model_map.get(r["model_id"], r["model_id"]),
            "status": r["status"],
            "garak_version": r.get("garak_version") or "",
            "created_at": r["created_at"].isoformat() if pd.notna(r["created_at"]) else "",
            "completed_at": r["completed_at"].isoformat() if pd.notna(r.get("completed_at")) else "",
        })
    return rows


def get_run_probe_results(run_id: str) -> list[dict]:
    """All probe results for a specific run."""
    pr = _probe_results[_probe_results["run_id"] == run_id]
    return [
        {
            "probe_name": r["probe_name"],
            "probe_category": r["probe_category"],
            "detector": r["detector"],
            "pass_count": int(r["pass_count"]),
            "fail_count": int(r["fail_count"]),
            "score": float(r["score"]) if pd.notna(r.get("score")) else None,
        }
        for _, r in pr.iterrows()
    ]
