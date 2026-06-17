"""
CTI-lite data layer.

Loads models, cti_runs, and cti_results from a HuggingFace Dataset into
in-memory pandas DataFrames.  All query functions operate on these DataFrames
directly — no HTTP API, no database.

Set HF_CTI_DATASET_REPO and optionally HF_TOKEN before running.
"""

import json
import os

import pandas as pd

HF_CTI_DATASET_REPO: str = os.environ.get("HF_CTI_DATASET_REPO", "")
HF_TOKEN: str | None = os.environ.get("HF_TOKEN") or None

_TASK_ORDER = ["rcm", "vsp", "ate", "taa", "forecast", "syn"]

# Module-level cache — populated by load_data()
_models: pd.DataFrame = pd.DataFrame()
_runs: pd.DataFrame = pd.DataFrame()
_results: pd.DataFrame = pd.DataFrame()


def load_data() -> None:
    """Load models, cti_runs, and cti_results from the HF Dataset into memory.

    Safe to call multiple times (re-loads on each call).
    Raises RuntimeError if the dataset cannot be reached or repo is unset.
    """
    global _models, _runs, _results

    if not HF_CTI_DATASET_REPO:
        raise RuntimeError("HF_CTI_DATASET_REPO is not set.")

    try:
        from datasets import load_dataset
        models_ds = load_dataset(HF_CTI_DATASET_REPO, name="models", token=HF_TOKEN)
        runs_ds = load_dataset(HF_CTI_DATASET_REPO, name="cti_runs", token=HF_TOKEN)
        results_ds = load_dataset(HF_CTI_DATASET_REPO, name="cti_results", token=HF_TOKEN)
    except Exception as e:
        raise RuntimeError(f"Failed to load HF dataset '{HF_CTI_DATASET_REPO}': {e}") from e

    _models = models_ds["train"].to_pandas()
    _runs = runs_ds["train"].to_pandas()
    _results = results_ds["train"].to_pandas()

    # Normalise timestamps
    for col in ("completed_at", "started_at", "created_at"):
        if col in _runs.columns:
            _runs[col] = pd.to_datetime(_runs[col], utc=True, errors="coerce")
    if "created_at" in _results.columns:
        _results["created_at"] = pd.to_datetime(_results["created_at"], utc=True, errors="coerce")

    print(
        f"[data] Loaded {len(_models)} models, {len(_runs)} cti_runs, "
        f"{len(_results)} cti_results from {HF_CTI_DATASET_REPO}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_complete_runs() -> pd.DataFrame:
    """Return one row per (model_id, task): the most recent complete run."""
    complete = _runs[_runs["status"] == "complete"].copy()
    if complete.empty:
        return pd.DataFrame(columns=_runs.columns)
    idx = complete.groupby(["model_id", "task"])["completed_at"].idxmax()
    return complete.loc[idx].reset_index(drop=True)


def _score_summary(task: str, breakdown_json: str | None) -> str:
    if not breakdown_json:
        return ""
    try:
        bd = json.loads(breakdown_json)
    except Exception:
        return str(breakdown_json)
    try:
        if task in ("rcm", "ate"):
            return f"F1 p={bd['precision']:.2f} r={bd['recall']:.2f}"
        if task == "vsp":
            sev = "✓" if bd.get("severity_match") else "✗"
            return f"MAD={bd['mad']:.2f} sev={sev}"
        if task == "taa":
            return f"result={bd.get('result', '?')} pred={bd.get('pred_canonical', '?')}"
        if task == "forecast":
            return f"prob={bd.get('prob', '?'):.2f} brier={bd.get('brier', '?'):.3f}"
        if task == "syn":
            parts = []
            for key, label in [("recall", "recall"), ("faithfulness", "faith"), ("calibration", "cal")]:
                v = bd.get(key)
                if v is not None:
                    parts.append(f"{label}={v:.2f}")
            return " ".join(parts)
    except Exception:
        pass
    return str(bd)


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def get_models() -> list[dict]:
    if _models.empty:
        return []
    cols = [c for c in ("id", "name", "provider") if c in _models.columns]
    return _models[cols].to_dict(orient="records")


def get_leaderboard() -> list[dict]:
    """One row per model: prequential score per task + overall mean."""
    if _runs.empty or _models.empty:
        return []

    latest = _latest_complete_runs()
    if latest.empty:
        return []

    model_map = dict(zip(_models["id"], _models["name"]))
    provider_map = dict(zip(_models["id"], _models["provider"]))

    rows = []
    for model_id, group in latest.groupby("model_id"):
        task_scores: dict[str, float | None] = {}
        for _, run in group.iterrows():
            task_scores[run["task"]] = (
                float(run["prequential_score"]) if pd.notna(run.get("prequential_score")) else None
            )

        valid = [s for s in task_scores.values() if s is not None]
        overall = sum(valid) / len(valid) if valid else None

        rows.append({
            "model_id": str(model_id),
            "model_name": model_map.get(str(model_id), str(model_id)),
            "provider": provider_map.get(str(model_id), ""),
            "task_scores": task_scores,
            "overall": overall,
        })

    rows.sort(key=lambda r: r["overall"] if r["overall"] is not None else -1.0, reverse=True)
    return rows


def get_model_runs(model_id: str) -> list[dict]:
    """Latest complete run per task for one model, ordered by task."""
    if _runs.empty:
        return []

    latest = _latest_complete_runs()
    model_runs = latest[latest["model_id"] == model_id].copy()
    if model_runs.empty:
        return []

    # Sort by canonical task order
    task_rank = {t: i for i, t in enumerate(_TASK_ORDER)}
    model_runs["_rank"] = model_runs["task"].map(lambda t: task_rank.get(t, 99))
    model_runs = model_runs.sort_values("_rank")

    rows = []
    for _, run in model_runs.iterrows():
        config = {}
        if run.get("config") and pd.notna(run["config"]):
            try:
                config = json.loads(run["config"])
            except Exception:
                pass
        auc = config.get("auc") if run["task"] == "forecast" else None
        completed = ""
        if pd.notna(run.get("completed_at")):
            completed = str(run["completed_at"])[:19].replace("T", " ")
        rows.append({
            "run_id": str(run["id"]),
            "task": run["task"],
            "prequential_score": float(run["prequential_score"]) if pd.notna(run.get("prequential_score")) else None,
            "item_count": int(run.get("item_count", 0)),
            "scored_count": int(run.get("scored_count", 0)),
            "completed_at": completed,
            "auc": float(auc) if auc is not None else None,
        })
    return rows


def get_run_results(run_id: str) -> list[dict]:
    """Per-item results for a single CTI run."""
    if _results.empty:
        return []

    # Resolve the task for this run (needed for score_summary)
    task = ""
    if not _runs.empty:
        run_row = _runs[_runs["id"] == run_id]
        if not run_row.empty:
            task = str(run_row.iloc[0].get("task", ""))

    run_results = _results[_results["run_id"] == run_id]
    rows = []
    for _, r in run_results.iterrows():
        score = float(r["score"]) if pd.notna(r.get("score")) else None
        correct = r.get("correct")
        pre_cutoff = r.get("pre_cutoff")
        rows.append({
            "item_id": str(r["item_id"]),
            "score": score,
            "correct": bool(correct) if correct is not None and pd.notna(correct) else None,
            "pre_cutoff": bool(pre_cutoff) if pre_cutoff is not None and pd.notna(pre_cutoff) else None,
            "score_summary": _score_summary(task, r.get("score_breakdown")),
        })
    return rows
