"""
Glokta Gradio Dashboard — read-only leaderboard UI.

Connects to the Glokta REST API to fetch and display:
- Leaderboard table with probe category and model filters
- Per-model probe breakdown when a model is selected
- Attempt-level drill-down with JSON viewer when a probe row is clicked
"""

import json

import httpx
import gradio as gr
import pandas as pd
from glokta.config import settings

API_BASE = settings.api_base_url

_PROBE_DETAIL_COLS = ["Probe Name", "Category", "Detector", "Pass", "Fail", "Score", "Pass Rate"]


def _probe_row(pr: dict) -> dict:
    total = pr["pass_count"] + pr["fail_count"]
    pass_rate = pr["pass_count"] / total if total > 0 else 0.0
    return {
        "Probe Name": pr["probe_name"],
        "Category": pr["probe_category"],
        "Detector": pr["detector"],
        "Pass": pr["pass_count"],
        "Fail": pr["fail_count"],
        "Score": f"{pr['score']:.3f}" if pr.get("score") is not None else "N/A",
        "Pass Rate": f"{pass_rate:.1%}",
    }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> dict | list | None:
    """GET request to the API; returns parsed JSON or None on error."""
    try:
        response = httpx.get(f"{API_BASE}{path}", params=params, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"API error: {exc}")
        return None


def fetch_probe_categories() -> list[str]:
    """Return all unique probe categories from the leaderboard."""
    data = _get("/api/leaderboard", params={"page_size": 200})
    if not data or not data.get("rows"):
        return []
    categories = sorted({row["probe_category"] for row in data["rows"]})
    return ["All"] + categories


def fetch_models() -> list[tuple[str, str]]:
    """Return list of (display_name, model_id) tuples for the model dropdown."""
    data = _get("/api/models")
    if not data:
        return []
    return [("All", "")] + [(m["name"], str(m["id"])) for m in data]


def fetch_leaderboard(probe_category: str, model_id: str) -> pd.DataFrame:
    """
    Fetch leaderboard rows filtered by probe_category and model.
    Returns a pandas DataFrame ready for gr.Dataframe display.
    """
    params: dict = {"page_size": 100}

    if probe_category and probe_category != "All":
        params["probe_category"] = probe_category

    if model_id:
        params["model_id"] = model_id

    data = _get("/api/leaderboard", params=params)
    if not data or not data.get("rows"):
        return pd.DataFrame(
            columns=["Model", "Provider", "Probe Category", "Pass", "Fail", "Score", "Pass Rate"]
        )

    rows = []
    for row in data["rows"]:
        rows.append({
            "Model": row["model_name"],
            "Provider": row["provider"],
            "Probe Category": row["probe_category"],
            "Pass": row["total_pass"],
            "Fail": row["total_fail"],
            "Score": f"{row['score']:.3f}" if row["score"] is not None else "N/A",
            "Pass Rate": f"{row['pass_rate']:.1%}",
            "Origin": row.get("origin", "api"),
        })

    return pd.DataFrame(rows)


def fetch_run_summary() -> pd.DataFrame:
    """Fetch per-model run status counts from the API."""
    data = _get("/api/runs/summary/by-model")
    if not data:
        return pd.DataFrame(
            columns=["Model", "Provider", "Complete", "Running", "Pending", "Failed", "Latest Origin"]
        )

    rows = [
        {
            "Model": r["model_name"],
            "Provider": r["provider"],
            "Complete": r["complete"],
            "Running": r["running"],
            "Pending": r["pending"],
            "Failed": r["failed"],
            "Latest Origin": r.get("latest_origin", "api"),
        }
        for r in data
    ]
    return pd.DataFrame(rows)


def fetch_runs(status_filter: str = "All") -> pd.DataFrame:
    """Fetch the most recent runs as a flat list for the Run Detail tab."""
    params = {"status": status_filter} if status_filter != "All" else None
    data = _get("/api/runs", params=params)
    if not data:
        return pd.DataFrame(columns=["Run ID", "Model", "Status", "Garak Version", "Created", "Completed"])

    model_names = {mid: name for name, mid in fetch_models() if mid}

    rows = []
    for r in data[:200]:
        mid = str(r.get("model_id", ""))
        rows.append({
            "Run ID": str(r["id"]),
            "Model": model_names.get(mid, mid),
            "Status": r["status"],
            "Garak Version": r.get("garak_version") or "—",
            "Created": r["created_at"][:19].replace("T", " "),
            "Completed": (r.get("completed_at") or "")[:19].replace("T", " ") or "—",
        })
    return pd.DataFrame(rows)


def fetch_run_detail(run_id: str) -> tuple[pd.DataFrame, str]:
    """Return (probe_results_df, raw_output_text) for a given run_id."""
    empty_df = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not run_id:
        return empty_df, ""

    probe_data = _get(f"/api/runs/{run_id}/probe-results")
    run_data = _get(f"/api/runs/{run_id}")

    rows = [_probe_row(pr) for pr in (probe_data or [])]
    raw = (run_data or {}).get("raw_output") or "(no output stored for this run)"
    return pd.DataFrame(rows) if rows else empty_df, raw


def fetch_model_detail(model_id: str) -> tuple[pd.DataFrame, str | None]:
    """Return (probe_df, run_id_str) for the most recent complete run of a model."""
    empty = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not model_id:
        return empty, None

    data = _get(f"/api/leaderboard/{model_id}")
    if not data or not data.get("probe_results"):
        return empty, None

    rows = [_probe_row(pr) for pr in data["probe_results"]]
    return pd.DataFrame(rows), data.get("run_id")


def fetch_attempts_json(run_id: str | None, probe_name: str | None) -> str:
    """Return pretty-printed JSON of attempts for a given run + probe_name."""
    if not run_id or not probe_name:
        return ""
    params = {"probe_name": probe_name}
    data = _get(f"/api/runs/{run_id}/attempts", params=params)
    if not data:
        return json.dumps({"message": "No attempts found"}, indent=2)
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    """Build and return the Gradio Blocks application."""

    with gr.Blocks(title="Glokta — LLM Security Leaderboard", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            """
            # 🔒 Glokta — LLM Security Leaderboard
            Powered by [garak](https://github.com/NVIDIA/garak) · OpenRouter free-tier models
            """
        )

        # Hidden state: run_id for the currently-selected model detail
        current_run_id = gr.State(value=None)

        with gr.Tabs():
            # ----------------------------------------------------------------
            # Tab 1: Leaderboard
            # ----------------------------------------------------------------
            with gr.Tab("Leaderboard"):
                with gr.Row():
                    category_filter = gr.Dropdown(
                        label="Probe Category",
                        choices=["All"],
                        value="All",
                        interactive=True,
                        scale=2,
                    )
                    model_filter = gr.Dropdown(
                        label="Model",
                        choices=[("All", "")],
                        value="",
                        interactive=True,
                        scale=3,
                    )
                    refresh_btn = gr.Button("🔄 Refresh", scale=1, variant="secondary")

                leaderboard_table = gr.Dataframe(
                    label="Leaderboard",
                    interactive=False,
                    wrap=True,
                )

                gr.Markdown("### Per-Model Probe Breakdown")
                gr.Markdown("*Select a model from the dropdown above to see its probe breakdown. Click a probe row to inspect attempts.*")

                detail_table = gr.Dataframe(
                    label="Probe Details (click a row to see attempts)",
                    interactive=False,
                    wrap=True,
                )

                gr.Markdown("### Attempt Detail")
                with gr.Row():
                    selected_probe_label = gr.Textbox(
                        label="Selected Probe",
                        interactive=False,
                        scale=3,
                    )
                attempts_viewer = gr.Code(
                    label="Attempts JSON",
                    language="json",
                    interactive=False,
                )

            # ----------------------------------------------------------------
            # Tab 2: Run Status
            # ----------------------------------------------------------------
            with gr.Tab("Run Status"):
                gr.Markdown("Per-model scan status. Refreshes automatically every 30 seconds while runs are active.")

                run_summary_table = gr.Dataframe(
                    label="Run Status by Model",
                    interactive=False,
                    wrap=True,
                )

                run_refresh_btn = gr.Button("🔄 Refresh Now", variant="secondary")
                run_timer = gr.Timer(value=30, active=True)

            # ----------------------------------------------------------------
            # Tab 3: Run Detail
            # ----------------------------------------------------------------
            with gr.Tab("Run Detail"):
                gr.Markdown(
                    "Select a run to inspect its probe results and raw garak JSONL output. "
                    "Click any row in the table below."
                )
                with gr.Row():
                    status_filter = gr.Dropdown(
                        label="Filter by status",
                        choices=["All", "complete", "failed", "running", "pending"],
                        value="All",
                        interactive=True,
                        scale=1,
                    )
                    runs_refresh_btn = gr.Button("🔄 Refresh", scale=1, variant="secondary")

                runs_table = gr.Dataframe(
                    label="Runs (click a row to inspect)",
                    interactive=False,
                    wrap=False,
                )

                selected_run_id = gr.Textbox(label="Selected Run ID", interactive=False)

                gr.Markdown("### Probe Results")
                run_probe_table = gr.Dataframe(
                    label="Probe Results (click a row to see attempts)",
                    interactive=False,
                    wrap=True,
                )

                gr.Markdown("### Attempt Detail")
                with gr.Row():
                    run_selected_probe_label = gr.Textbox(
                        label="Selected Probe",
                        interactive=False,
                        scale=3,
                    )
                run_attempts_viewer = gr.Code(
                    label="Attempts JSON",
                    language="json",
                    interactive=False,
                )

                gr.Markdown("### Raw JSONL Output")
                raw_output_box = gr.Code(
                    label="Raw garak JSONL",
                    language="json",
                    interactive=False,
                )

        # --- Event: initial load ---
        def on_load():
            categories = fetch_probe_categories()
            models = fetch_models()
            df = fetch_leaderboard("All", "")
            summary_df = fetch_run_summary()
            runs_df = fetch_runs("All")
            return (
                gr.update(choices=categories, value="All"),
                gr.update(choices=models, value=""),
                df,
                summary_df,
                runs_df,
            )

        # --- Event: filter change ---
        def on_filter_change(probe_category: str, model_id: str):
            df = fetch_leaderboard(probe_category, model_id)
            if model_id:
                detail_df, run_id = fetch_model_detail(model_id)
            else:
                detail_df = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
                run_id = None
            return df, detail_df, run_id, "", ""

        # --- Event: probe row click in leaderboard detail table ---
        def on_probe_select(evt: gr.SelectData, detail_df: pd.DataFrame, run_id: str | None):
            try:
                row = detail_df.iloc[evt.index[0]]
                probe_name = str(row["Probe Name"])
            except Exception:
                return "", ""
            attempts_json = fetch_attempts_json(run_id, probe_name)
            return probe_name, attempts_json

        # --- Run Detail tab events ---
        def on_run_select(evt: gr.SelectData, runs_df: pd.DataFrame):
            """Handle row click in the runs table — load probe results and raw output."""
            try:
                run_id = str(runs_df.iloc[evt.index[0]]["Run ID"])
            except Exception:
                return "", pd.DataFrame(columns=_PROBE_DETAIL_COLS), "", "", ""
            probe_df, raw = fetch_run_detail(run_id)
            return run_id, probe_df, raw, "", ""

        def on_run_probe_select(evt: gr.SelectData, probe_df: pd.DataFrame, run_id: str):
            """Handle row click in the run probe table — fetch attempts."""
            try:
                row = probe_df.iloc[evt.index[0]]
                probe_name = str(row["Probe Name"])
            except Exception:
                return "", ""
            attempts_json = fetch_attempts_json(run_id, probe_name)
            return probe_name, attempts_json

        def on_runs_filter(status: str):
            return fetch_runs(status)

        # Wire events
        demo.load(
            fn=on_load,
            inputs=None,
            outputs=[category_filter, model_filter, leaderboard_table, run_summary_table, runs_table],
        )

        refresh_btn.click(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id, selected_probe_label, attempts_viewer],
        )

        category_filter.change(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id, selected_probe_label, attempts_viewer],
        )

        model_filter.change(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id, selected_probe_label, attempts_viewer],
        )

        detail_table.select(
            fn=on_probe_select,
            inputs=[detail_table, current_run_id],
            outputs=[selected_probe_label, attempts_viewer],
        )

        run_refresh_btn.click(
            fn=fetch_run_summary,
            inputs=None,
            outputs=[run_summary_table],
        )

        run_timer.tick(
            fn=fetch_run_summary,
            inputs=None,
            outputs=[run_summary_table],
        )

        runs_table.select(
            fn=on_run_select,
            inputs=[runs_table],
            outputs=[selected_run_id, run_probe_table, raw_output_box, run_selected_probe_label, run_attempts_viewer],
        )

        run_probe_table.select(
            fn=on_run_probe_select,
            inputs=[run_probe_table, selected_run_id],
            outputs=[run_selected_probe_label, run_attempts_viewer],
        )

        status_filter.change(
            fn=on_runs_filter,
            inputs=[status_filter],
            outputs=[runs_table],
        )

        runs_refresh_btn.click(
            fn=on_runs_filter,
            inputs=[status_filter],
            outputs=[runs_table],
        )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=settings.gradio_server_port,
        show_api=False,
    )
