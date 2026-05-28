"""
Glokta Gradio Dashboard — read-only security leaderboard UI.

Five tabs:
  1. Risk Leaderboard — risk-weighted pass rates; click a row to drill into Probe Results
  2. Probe Results   — raw probe-level data (original leaderboard view)
  3. Compare         — overall pass rate across multiple models over time
  4. Run Status      — per-model scan status
  5. Run Detail      — per-run probe results and raw JSONL output
"""

import json

import httpx
import gradio as gr
import pandas as pd
import plotly.graph_objects as go

from glokta.config import settings
from glokta.risks import ACTIVE_RISKS, RISK_DEFINITIONS

API_BASE = settings.api_base_url

_PROBE_DETAIL_COLS = ["Probe Name", "Category", "Detector", "Pass", "Fail", "ASR", "Pass Rate"]

_RISK_CHECKBOX_CHOICES = [(v["label"], k) for k, v in RISK_DEFINITIONS.items() if v["enabled"]]
_RISK_CHECKBOX_DEFAULT = ACTIVE_RISKS


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
        print(f"API error {path}: {exc}")
        return None


def _probe_row(pr: dict) -> dict:
    total = pr["pass_count"] + pr["fail_count"]
    pass_rate = pr["pass_count"] / total if total > 0 else 0.0
    return {
        "Probe Name": pr["probe_name"],
        "Category": pr["probe_category"],
        "Detector": pr["detector"],
        "Pass": pr["pass_count"],
        "Fail": pr["fail_count"],
        "ASR": f"{pr['score']:.3f}" if pr.get("score") is not None else "N/A",
        "Pass Rate": f"{pass_rate:.1%}",
    }


# ---------------------------------------------------------------------------
# Data fetch helpers
# ---------------------------------------------------------------------------

def fetch_probe_categories() -> list[str]:
    data = _get("/api/leaderboard", params={"page_size": 200})
    if not data or not data.get("rows"):
        return []
    categories = sorted({row["probe_category"] for row in data["rows"]})
    return ["All"] + categories


def fetch_models() -> list[tuple[str, str]]:
    """Return (display_name, model_id) tuples."""
    data = _get("/api/models")
    if not data:
        return []
    return [(m["name"], str(m["id"])) for m in data]


def fetch_leaderboard(probe_category: str, model_id: str) -> pd.DataFrame:
    params: dict = {"page_size": 100}
    if probe_category and probe_category != "All":
        params["probe_category"] = probe_category
    if model_id:
        params["model_id"] = model_id

    data = _get("/api/leaderboard", params=params)
    if not data or not data.get("rows"):
        return pd.DataFrame(columns=["Model", "Provider", "Probe Category", "Pass", "Fail", "ASR", "Pass Rate"])

    rows = []
    for row in data["rows"]:
        rows.append({
            "Model": row["model_name"],
            "Provider": row["provider"],
            "Probe Category": row["probe_category"],
            "Pass": row["total_pass"],
            "Fail": row["total_fail"],
            "ASR": f"{row['score']:.3f}" if row["score"] is not None else "N/A",
            "Pass Rate": f"{row['pass_rate']:.1%}",
            "Origin": row.get("origin", "api"),
        })
    return pd.DataFrame(rows)


def fetch_risk_leaderboard(included_risks: list[str]) -> pd.DataFrame:
    """Fetch risk-based leaderboard — Model, Provider, Overall Pass Rate only."""
    empty = pd.DataFrame(columns=["Model", "Provider", "Overall Pass Rate"])

    if not included_risks:
        return empty

    params = {"included_risks": ",".join(included_risks)}
    data = _get("/api/risk-leaderboard", params=params)
    if not data or not data.get("models"):
        return empty

    rows = [
        {
            "Model": m["model_name"],
            "Provider": m["provider"],
            "Overall Pass Rate": f"{m['overall_pass_rate']:.1%}" if m["overall_pass_rate"] is not None else "N/A",
        }
        for m in data["models"]
    ]
    return pd.DataFrame(rows) if rows else empty


def fetch_trends_for_model(model_id: str, included_risks: list[str]) -> tuple[list[dict], str] | None:
    """Return (trend_points, model_name) or None when no data is available."""
    if not model_id or not included_risks:
        return None
    params = {"included_risks": ",".join(included_risks)}
    data = _get(f"/api/trends/{model_id}", params=params)
    if not data or not data.get("points"):
        return None
    return data["points"], data.get("model_name", model_id)


def fetch_run_summary() -> pd.DataFrame:
    data = _get("/api/runs/summary/by-model")
    if not data:
        return pd.DataFrame(columns=["Model", "Provider", "Complete", "Running", "Pending", "Failed", "Latest Origin"])
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
    empty_df = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not run_id:
        return empty_df, ""
    probe_data = _get(f"/api/runs/{run_id}/probe-results")
    run_data = _get(f"/api/runs/{run_id}")
    rows = [_probe_row(pr) for pr in (probe_data or [])]
    raw = (run_data or {}).get("raw_output") or "(no output stored for this run)"
    return pd.DataFrame(rows) if rows else empty_df, raw


def fetch_model_detail(model_id: str) -> tuple[pd.DataFrame, str | None]:
    empty = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not model_id:
        return empty, None
    data = _get(f"/api/leaderboard/{model_id}")
    if not data or not data.get("probe_results"):
        return empty, None
    rows = [_probe_row(pr) for pr in data["probe_results"]]
    return pd.DataFrame(rows), data.get("run_id")


def fetch_attempts_json(run_id: str | None, probe_name: str | None) -> str:
    if not run_id or not probe_name:
        return ""
    params = {"probe_name": probe_name}
    data = _get(f"/api/runs/{run_id}/attempts", params=params)
    if not data:
        return json.dumps({"message": "No attempts found"}, indent=2)
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _empty_fig(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False, font=dict(size=14),
    )
    fig.update_layout(xaxis_visible=False, yaxis_visible=False)
    return fig


def make_compare_plot(
    model_entries: list[tuple[str, str]],
    included_risks: list[str],
) -> go.Figure:
    """Line chart: one overall-pass-rate series per selected model over time."""
    fig = go.Figure()
    plotted = 0

    for model_id, model_name in model_entries:
        result = fetch_trends_for_model(model_id, included_risks)
        if not result:
            continue
        points, _ = result
        if not points:
            continue

        dates = [p["completed_at"] for p in points]
        y_vals = [p.get("overall_pass_rate") for p in points]

        if any(v is not None for v in y_vals):
            fig.add_trace(go.Scatter(
                x=dates, y=y_vals,
                mode="lines+markers",
                name=model_name,
                connectgaps=True,
            ))
            plotted += 1

    if plotted == 0:
        return _empty_fig("No scan history for selected models.")

    fig.update_layout(
        title="Model Comparison — Overall Risk Pass Rate Over Time",
        xaxis_title="Scan Date",
        yaxis=dict(title="Pass Rate", range=[0, 1], tickformat=".0%"),
    )
    return fig


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    """Build and return the Gradio Blocks application."""

    with gr.Blocks(title="Glokta — LLM Security Leaderboard", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            """
            # Glokta — LLM Security Leaderboard
            Powered by [garak](https://github.com/NVIDIA/garak) · OpenRouter free-tier models
            """
        )

        # Shared state
        current_run_id = gr.State(value=None)
        # Maps model display name → model UUID for row-click navigation
        model_name_to_id = gr.State(value={})

        with gr.Tabs() as tabs:

            # ----------------------------------------------------------------
            # Tab 1: Risk Leaderboard
            # ----------------------------------------------------------------
            with gr.Tab("Risk Leaderboard", id="risk_leaderboard"):
                gr.Markdown(
                    "Overall pass rate = mean of per-risk pass rates for selected risks. "
                    "Sorted safest-first. **Click a row to drill into probe results.**"
                )
                with gr.Row():
                    risk_filter = gr.CheckboxGroup(
                        label="Include Risks",
                        choices=_RISK_CHECKBOX_CHOICES,
                        value=_RISK_CHECKBOX_DEFAULT,
                        scale=4,
                    )
                    risk_refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")

                gr.Markdown(
                    "_fileformats (RCE via Model Artifacts) is excluded — silently fails with REST generators; no scan data._",
                )

                risk_table = gr.Dataframe(
                    label="Risk Leaderboard",
                    interactive=False,
                    wrap=True,
                )

            # ----------------------------------------------------------------
            # Tab 2: Probe Results
            # ----------------------------------------------------------------
            with gr.Tab("Probe Results", id="probe_results") as probe_tab:
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
                    refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")

                leaderboard_table = gr.Dataframe(
                    label="Probe Results",
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
                selected_probe_label = gr.Textbox(
                    label="Selected Probe",
                    interactive=False,
                )
                attempts_viewer = gr.Code(
                    label="Attempts JSON",
                    language="json",
                    interactive=False,
                )

            # ----------------------------------------------------------------
            # Tab 3: Compare
            # ----------------------------------------------------------------
            with gr.Tab("Compare", id="compare"):
                gr.Markdown(
                    "Overall pass rate across multiple models over time. "
                    "Risk filter affects the overall pass rate calculation."
                )
                with gr.Row():
                    compare_models = gr.Dropdown(
                        label="Models (select multiple)",
                        choices=[],
                        value=[],
                        multiselect=True,
                        interactive=True,
                        scale=4,
                    )
                    compare_refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")

                compare_risk_filter = gr.CheckboxGroup(
                    label="Risk Categories",
                    choices=_RISK_CHECKBOX_CHOICES,
                    value=_RISK_CHECKBOX_DEFAULT,
                )

                compare_plot = gr.Plot(label="Model Comparison")

            # ----------------------------------------------------------------
            # Tab 4: Run Status
            # ----------------------------------------------------------------
            with gr.Tab("Run Status", id="run_status"):
                gr.Markdown("Per-model scan status. Refreshes automatically every 30 seconds.")

                run_summary_table = gr.Dataframe(
                    label="Run Status by Model",
                    interactive=False,
                    wrap=True,
                )

                run_refresh_btn = gr.Button("Refresh Now", variant="secondary")
                run_timer = gr.Timer(value=30, active=True)

            # ----------------------------------------------------------------
            # Tab 5: Run Detail
            # ----------------------------------------------------------------
            with gr.Tab("Run Detail", id="run_detail"):
                gr.Markdown("Select a run to inspect its probe results and raw garak JSONL output.")
                with gr.Row():
                    status_filter = gr.Dropdown(
                        label="Filter by status",
                        choices=["All", "complete", "failed", "running", "pending"],
                        value="All",
                        interactive=True,
                        scale=1,
                    )
                    runs_refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")

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
                run_selected_probe_label = gr.Textbox(
                    label="Selected Probe",
                    interactive=False,
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

        # --------------------------------------------------------------------
        # Event handlers
        # --------------------------------------------------------------------

        def on_load():
            categories = fetch_probe_categories()
            models = fetch_models()
            model_choices_with_all = [("All", "")] + models
            name_to_id = {name: mid for name, mid in models}
            leaderboard_df = fetch_leaderboard("All", "")
            risk_df = fetch_risk_leaderboard(_RISK_CHECKBOX_DEFAULT)
            summary_df = fetch_run_summary()
            runs_df = fetch_runs("All")
            return (
                gr.update(choices=categories, value="All"),           # category_filter
                gr.update(choices=model_choices_with_all, value=""),  # model_filter
                leaderboard_df,                                        # leaderboard_table
                risk_df,                                               # risk_table
                name_to_id,                                            # model_name_to_id
                gr.update(choices=models, value=[]),                  # compare_models
                summary_df,                                            # run_summary_table
                runs_df,                                               # runs_table
            )

        def on_probe_filter_change(probe_category: str, model_id: str):
            df = fetch_leaderboard(probe_category, model_id)
            if model_id:
                detail_df, run_id = fetch_model_detail(model_id)
            else:
                detail_df = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
                run_id = None
            return df, detail_df, run_id, "", ""

        def on_probe_select(evt: gr.SelectData, detail_df: pd.DataFrame, run_id: str | None):
            try:
                row = detail_df.iloc[evt.index[0]]
                probe_name = str(row["Probe Name"])
            except Exception:
                return "", ""
            return probe_name, fetch_attempts_json(run_id, probe_name)

        def on_risk_filter_change(included_risks: list[str]):
            return fetch_risk_leaderboard(included_risks)

        def on_risk_row_click(evt: gr.SelectData, risk_df: pd.DataFrame, name_to_id: dict):
            """Set model filter and switch to Probe Results tab.

            Only updates model_filter, category_filter, and tabs — never touches
            tab-internal display components simultaneously, which corrupts Gradio 5
            session state when a parent (tabs) and its children are updated together.
            The probe_tab.select event then fires and loads the actual data.
            """
            try:
                model_name = str(risk_df.iloc[evt.index[0]]["Model"])
            except Exception:
                return gr.update(), gr.update(), gr.update()

            model_id = name_to_id.get(model_name, "")
            return (
                gr.update(value=model_id),                # model_filter
                gr.update(value="All"),                   # category_filter
                gr.update(selected="probe_results"),      # tabs → switch tab
            )

        def on_compare_update(selected_values: list[str], included_risks: list[str]):
            if not selected_values or not included_risks:
                return _empty_fig("Select models to compare.")
            all_models = fetch_models()
            id_to_name = {mid: name for name, mid in all_models}
            entries = [(mid, id_to_name.get(mid, mid)) for mid in selected_values]
            return make_compare_plot(entries, included_risks)

        def on_run_select(evt: gr.SelectData, runs_df: pd.DataFrame):
            try:
                run_id = str(runs_df.iloc[evt.index[0]]["Run ID"])
            except Exception:
                return "", pd.DataFrame(columns=_PROBE_DETAIL_COLS), "", "", ""
            probe_df, raw = fetch_run_detail(run_id)
            return run_id, probe_df, raw, "", ""

        def on_run_probe_select(evt: gr.SelectData, probe_df: pd.DataFrame, run_id: str):
            try:
                row = probe_df.iloc[evt.index[0]]
                probe_name = str(row["Probe Name"])
            except Exception:
                return "", ""
            return probe_name, fetch_attempts_json(run_id, probe_name)

        # --------------------------------------------------------------------
        # Wire events
        # --------------------------------------------------------------------

        demo.load(
            fn=on_load,
            inputs=None,
            outputs=[
                category_filter, model_filter, leaderboard_table,
                risk_table, model_name_to_id,
                compare_models,
                run_summary_table, runs_table,
            ],
        )

        # Risk Leaderboard tab
        risk_filter.change(fn=on_risk_filter_change, inputs=[risk_filter], outputs=[risk_table])
        risk_refresh_btn.click(fn=on_risk_filter_change, inputs=[risk_filter], outputs=[risk_table])
        # Row click: only switch tab + set filter values; probe_tab.select handles the data load
        risk_table.select(
            fn=on_risk_row_click,
            inputs=[risk_table, model_name_to_id],
            outputs=[model_filter, category_filter, tabs],
        )

        # Probe Results tab
        # probe_tab.select fires on normal navigation AND after programmatic tab switch,
        # so it handles both the "jump from risk leaderboard" and "user clicks tab" cases.
        probe_tab.select(
            fn=on_probe_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id, selected_probe_label, attempts_viewer],
        )
        refresh_btn.click(
            fn=on_probe_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id, selected_probe_label, attempts_viewer],
        )
        detail_table.select(
            fn=on_probe_select,
            inputs=[detail_table, current_run_id],
            outputs=[selected_probe_label, attempts_viewer],
        )

        # Compare tab
        compare_models.change(fn=on_compare_update, inputs=[compare_models, compare_risk_filter], outputs=[compare_plot])
        compare_risk_filter.change(fn=on_compare_update, inputs=[compare_models, compare_risk_filter], outputs=[compare_plot])
        compare_refresh_btn.click(fn=on_compare_update, inputs=[compare_models, compare_risk_filter], outputs=[compare_plot])

        # Run Status tab
        run_refresh_btn.click(fn=fetch_run_summary, inputs=None, outputs=[run_summary_table])
        run_timer.tick(fn=fetch_run_summary, inputs=None, outputs=[run_summary_table])

        # Run Detail tab
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
        status_filter.change(fn=fetch_runs, inputs=[status_filter], outputs=[runs_table])
        runs_refresh_btn.click(fn=fetch_runs, inputs=[status_filter], outputs=[runs_table])

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
