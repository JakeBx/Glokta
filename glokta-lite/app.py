"""
Glokta-lite — Gradio dashboard backed directly by the HF Dataset.

Four tabs:
  1. Risk Leaderboard — risk-weighted pass rates; click a row to drill into Probe Results
  2. Probe Results   — raw probe-level data per model
  3. Compare         — overall pass rate across multiple models over time
  4. Run Status      — per-model scan status summary

No database, no API server, no attempts data.
Set HF_DATASET_REPO (default: Jake/glokta-public) before running.
"""

import os

import gradio as gr
import pandas as pd
import plotly.graph_objects as go

import data
from risks import ACTIVE_RISKS, RISK_DEFINITIONS

_PROBE_DETAIL_COLS = ["Probe Name", "Category", "Detector", "Pass", "Fail", "ASR", "Pass Rate"]

_RISK_CHECKBOX_CHOICES = [(v["label"], k) for k, v in RISK_DEFINITIONS.items() if v["enabled"]]
_RISK_CHECKBOX_DEFAULT = ACTIVE_RISKS


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

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


def _empty_fig(message: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False, font=dict(size=14),
    )
    fig.update_layout(xaxis_visible=False, yaxis_visible=False)
    return fig


# ---------------------------------------------------------------------------
# DataFrame builders (mirror the main app's fetch_* functions)
# ---------------------------------------------------------------------------

def build_leaderboard_df(probe_category: str, model_id: str) -> pd.DataFrame:
    rows = data.get_leaderboard(
        probe_category=probe_category if probe_category and probe_category != "All" else None,
        model_id=model_id if model_id else None,
    )
    if not rows:
        return pd.DataFrame(columns=["Model", "Provider", "Probe Category", "Pass", "Fail", "ASR", "Pass Rate"])
    return pd.DataFrame([{
        "Model": r["model_name"],
        "Provider": r["provider"],
        "Probe Category": r["probe_category"],
        "Pass": r["total_pass"],
        "Fail": r["total_fail"],
        "ASR": f"{r['score']:.3f}" if r["score"] is not None else "N/A",
        "Pass Rate": f"{r['pass_rate']:.1%}",
        "Origin": r.get("origin", "api"),
    } for r in rows])


def build_risk_leaderboard_df(included_risks: list[str]) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["Model", "Provider", "Overall Pass Rate", "Coverage"])
    if not included_risks:
        return empty
    rows = data.get_risk_leaderboard(included_risks)
    if not rows:
        return empty
    result = []
    for m in rows:
        per_risk = m.get("per_risk", {})
        n_covered = sum(1 for r in included_risks if per_risk.get(r) is not None)
        overall_str = f"{m['overall_pass_rate']:.1%}" if m["overall_pass_rate"] is not None else "N/A"
        coverage_str = f"{n_covered}/{len(included_risks)}" if n_covered < len(included_risks) else "✓"
        result.append({
            "Model": m["model_name"],
            "Provider": m["provider"],
            "Overall Pass Rate": overall_str,
            "Coverage": coverage_str,
        })
    return pd.DataFrame(result)


def build_model_detail_df(model_id: str) -> tuple[pd.DataFrame, str | None]:
    empty = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not model_id:
        return empty, None
    detail = data.get_model_detail(model_id)
    if not detail or not detail.get("probe_results"):
        return empty, None
    rows = [_probe_row(pr) for pr in detail["probe_results"]]
    return pd.DataFrame(rows), detail.get("run_id")


def build_run_summary_df() -> pd.DataFrame:
    rows = data.get_run_summary()
    if not rows:
        return pd.DataFrame(columns=["Model", "Provider", "Complete", "Running", "Pending", "Failed", "Latest Origin"])
    return pd.DataFrame([{
        "Model": r["model_name"],
        "Provider": r["provider"],
        "Complete": r["complete"],
        "Running": r["running"],
        "Pending": r["pending"],
        "Failed": r["failed"],
        "Latest Origin": r.get("latest_origin", ""),
    } for r in rows])


def build_run_probe_df(run_id: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
    if not run_id:
        return empty
    rows = data.get_run_probe_results(run_id)
    return pd.DataFrame([_probe_row(pr) for pr in rows]) if rows else empty


def make_compare_plot(model_ids: list[str], included_risks: list[str]) -> go.Figure:
    fig = go.Figure()
    plotted = 0
    for model_id in model_ids:
        result = data.get_trends(model_id, included_risks)
        if not result or not result.get("points"):
            continue
        points = result["points"]
        dates = [p["completed_at"] for p in points]
        y_vals = [p.get("overall_pass_rate") for p in points]
        if any(v is not None for v in y_vals):
            fig.add_trace(go.Scatter(
                x=dates, y=y_vals,
                mode="lines+markers",
                name=result["model_name"],
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
    with gr.Blocks(title="Glokta Lite", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            f"""
            # Glokta Lite — LLM Security Leaderboard
            [Main Project](https://github.com/JakeBx/Glokta) to reference for self-hosting options
            
            Powered by [garak](https://github.com/NVIDIA/garak) · data from `{data.HF_DATASET_REPO}`
            """
        )

        current_run_id = gr.State(value=None)
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

                risk_table = gr.Dataframe(label="Risk Leaderboard", interactive=False, wrap=True)

            # ----------------------------------------------------------------
            # Tab 2: Probe Results
            # ----------------------------------------------------------------
            with gr.Tab("Probe Results", id="probe_results"):
                with gr.Row():
                    category_filter = gr.Dropdown(
                        label="Probe Category", choices=["All"], value="All",
                        interactive=True, scale=2,
                    )
                    model_filter = gr.Dropdown(
                        label="Model", choices=[("All", "")], value="",
                        interactive=True, scale=3,
                    )
                    probe_refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")

                leaderboard_table = gr.Dataframe(label="Probe Results", interactive=False, wrap=True)

                gr.Markdown("### Per-Model Probe Breakdown")
                gr.Markdown("*Select a model from the dropdown above. Click a row to see attempt-level detail.*")
                detail_table = gr.Dataframe(
                    label="Probe Details", interactive=False, wrap=True,
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
                    compare_models_input = gr.Dropdown(
                        label="Models (select multiple)", choices=[], value=[],
                        multiselect=True, interactive=True, scale=4,
                    )
                    compare_refresh_btn = gr.Button("Refresh", scale=1, variant="secondary")
                compare_risk_filter = gr.CheckboxGroup(
                    label="Risk Categories",
                    choices=_RISK_CHECKBOX_CHOICES,
                    value=_RISK_CHECKBOX_DEFAULT,
                )
                compare_plot = gr.Plot(label="Model Comparison", value=_empty_fig("Select models to compare."))

            # ----------------------------------------------------------------
            # Tab 4: Run Status
            # ----------------------------------------------------------------
            with gr.Tab("Run Status", id="run_status"):
                gr.Markdown("Per-model scan status summary.")
                run_summary_table = gr.Dataframe(
                    label="Run Status by Model", interactive=False, wrap=True,
                )
                gr.Row()
                with gr.Row():
                    reload_btn = gr.Button("Reload Data from HF", variant="primary")
                    run_refresh_btn = gr.Button("Refresh Table", variant="secondary")

        # --------------------------------------------------------------------
        # Event handlers
        # --------------------------------------------------------------------

        def on_load():
            try:
                data.load_data()
            except Exception as exc:
                print(f"[app] Data load failed: {exc}")

            categories = ["All"] + data.get_probe_categories()
            models = [(m["name"], m["id"]) for m in data.get_models()]
            model_choices_with_all = [("All", "")] + models
            name_to_id = {m["name"]: m["id"] for m in data.get_models()}

            return (
                gr.update(choices=categories, value="All"),
                gr.update(choices=model_choices_with_all, value=""),
                build_leaderboard_df("All", ""),
                build_risk_leaderboard_df(_RISK_CHECKBOX_DEFAULT),
                name_to_id,
                gr.update(choices=models, value=[]),
                build_run_summary_df(),
            )

        def on_reload():
            try:
                data.load_data()
            except Exception as exc:
                print(f"[app] Reload failed: {exc}")
            return build_run_summary_df()

        def on_probe_filter_change(probe_category: str, model_id: str):
            df = build_leaderboard_df(probe_category, model_id)
            if model_id:
                detail_df, run_id = build_model_detail_df(model_id)
            else:
                detail_df = pd.DataFrame(columns=_PROBE_DETAIL_COLS)
                run_id = None
            return df, detail_df, run_id

        def on_risk_filter_change(included_risks: list[str]):
            return build_risk_leaderboard_df(included_risks)

        def on_risk_row_click(evt: gr.SelectData, risk_df: pd.DataFrame, name_to_id: dict):
            try:
                model_name = str(risk_df.iloc[evt.index[0]]["Model"]).strip()
            except Exception:
                return gr.update(), gr.update(), pd.DataFrame(columns=_PROBE_DETAIL_COLS), None, gr.update()
            model_id = name_to_id.get(model_name, "")
            leaderboard_df = build_leaderboard_df("All", model_id)
            detail_df, run_id = build_model_detail_df(model_id) if model_id else (pd.DataFrame(columns=_PROBE_DETAIL_COLS), None)
            return (
                gr.update(value=model_id),          # model_filter
                leaderboard_df,                      # leaderboard_table
                detail_df,                           # detail_table
                run_id,                              # current_run_id
                gr.update(selected="probe_results"), # tabs
            )

        def on_compare_update(selected_values: list[str], included_risks: list[str]):
            if not selected_values or not included_risks:
                return _empty_fig("Select models to compare.")
            return make_compare_plot(selected_values, included_risks)

        # --------------------------------------------------------------------
        # Wire events
        # --------------------------------------------------------------------

        demo.load(
            fn=on_load,
            inputs=None,
            outputs=[
                category_filter, model_filter, leaderboard_table,
                risk_table, model_name_to_id,
                compare_models_input,
                run_summary_table,
            ],
        )

        risk_filter.change(fn=on_risk_filter_change, inputs=[risk_filter], outputs=[risk_table])
        risk_refresh_btn.click(fn=on_risk_filter_change, inputs=[risk_filter], outputs=[risk_table])
        risk_table.select(
            fn=on_risk_row_click,
            inputs=[risk_table, model_name_to_id],
            outputs=[model_filter, leaderboard_table, detail_table, current_run_id, tabs],
        )

        probe_refresh_btn.click(
            fn=on_probe_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id],
        )
        category_filter.change(
            fn=on_probe_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id],
        )
        model_filter.change(
            fn=on_probe_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table, current_run_id],
        )

        compare_models_input.change(
            fn=on_compare_update, inputs=[compare_models_input, compare_risk_filter], outputs=[compare_plot]
        )
        compare_risk_filter.change(
            fn=on_compare_update, inputs=[compare_models_input, compare_risk_filter], outputs=[compare_plot]
        )
        compare_refresh_btn.click(
            fn=on_compare_update, inputs=[compare_models_input, compare_risk_filter], outputs=[compare_plot]
        )

        reload_btn.click(fn=on_reload, inputs=None, outputs=[run_summary_table])
        run_refresh_btn.click(fn=build_run_summary_df, inputs=None, outputs=[run_summary_table])

    return demo


if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7860))
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=port, show_api=False)
