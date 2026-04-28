"""
GarakBoard Gradio Dashboard — read-only leaderboard UI.

Connects to the GarakBoard REST API to fetch and display:
- Leaderboard table with probe category and model filters
- Per-model probe breakdown when a model is selected
"""

import httpx
import gradio as gr
import pandas as pd
from garakboard.config import settings

API_BASE = settings.api_base_url


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

    if model_id and model_id != "":
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
        })

    return pd.DataFrame(rows)


def fetch_run_summary() -> pd.DataFrame:
    """Fetch per-model run status counts from the API."""
    data = _get("/api/runs/summary/by-model")
    if not data:
        return pd.DataFrame(columns=["Model", "Provider", "Complete", "Running", "Pending", "Failed"])

    rows = [
        {
            "Model": r["model_name"],
            "Provider": r["provider"],
            "Complete": r["complete"],
            "Running": r["running"],
            "Pending": r["pending"],
            "Failed": r["failed"],
        }
        for r in data
    ]
    return pd.DataFrame(rows)


def fetch_model_detail(model_id: str) -> pd.DataFrame:
    """
    Fetch per-model probe breakdown for the detail panel.
    Returns a pandas DataFrame.
    """
    if not model_id:
        return pd.DataFrame(
            columns=["Probe Name", "Category", "Detector", "Pass", "Fail", "Score", "Pass Rate"]
        )

    data = _get(f"/api/leaderboard/{model_id}")
    if not data or not data.get("probe_results"):
        return pd.DataFrame(
            columns=["Probe Name", "Category", "Detector", "Pass", "Fail", "Score", "Pass Rate"]
        )

    rows = []
    for pr in data["probe_results"]:
        rows.append({
            "Probe Name": pr["probe_name"],
            "Category": pr["probe_category"],
            "Detector": pr["detector"],
            "Pass": pr["pass_count"],
            "Fail": pr["fail_count"],
            "Score": f"{pr['score']:.3f}" if pr["score"] is not None else "N/A",
            "Pass Rate": f"{pr['pass_rate']:.1%}",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    """Build and return the Gradio Blocks application."""

    with gr.Blocks(title="GarakBoard — LLM Security Leaderboard", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            """
            # 🔒 GarakBoard — LLM Security Leaderboard
            Powered by [garak](https://github.com/NVIDIA/garak) · OpenRouter free-tier models
            """
        )

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
                gr.Markdown("*Select a model from the dropdown above to see its probe breakdown.*")

                detail_table = gr.Dataframe(
                    label="Probe Details",
                    interactive=False,
                    wrap=True,
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

        # --- Event: initial load ---
        def on_load():
            categories = fetch_probe_categories()
            models = fetch_models()
            df = fetch_leaderboard("All", "")
            summary_df = fetch_run_summary()
            return (
                gr.update(choices=categories, value="All"),
                gr.update(choices=models, value=""),
                df,
                summary_df,
            )

        # --- Event: filter change ---
        def on_filter_change(probe_category: str, model_id: str):
            df = fetch_leaderboard(probe_category, model_id)
            if model_id and model_id != "":
                detail_df = fetch_model_detail(model_id)
            else:
                detail_df = pd.DataFrame(
                    columns=["Probe Name", "Category", "Detector", "Pass", "Fail", "Score", "Pass Rate"]
                )
            return df, detail_df

        # Wire events
        demo.load(
            fn=on_load,
            inputs=None,
            outputs=[category_filter, model_filter, leaderboard_table, run_summary_table],
        )

        refresh_btn.click(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table],
        )

        category_filter.change(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table],
        )

        model_filter.change(
            fn=on_filter_change,
            inputs=[category_filter, model_filter],
            outputs=[leaderboard_table, detail_table],
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