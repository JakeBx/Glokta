"""
CTI-lite — Gradio CTI benchmark dashboard backed directly by the HF Dataset.

Three tabs:
  1. CTI Leaderboard  — prequential score per task; click a row to drill into Model Detail
  2. CTI Model Detail — per-task scores + per-item results for one model
  3. Task Guide       — explainer for each of the six benchmark tasks

No database, no API server.
Set HF_CTI_DATASET_REPO (and optionally HF_TOKEN) before running.
"""

import os

import gradio as gr
import pandas as pd

import data

_TASK_ORDER = ["rcm", "vsp", "ate", "taa", "forecast", "syn"]
_TASK_LABELS = {"rcm": "RCM", "vsp": "VSP", "ate": "ATE", "taa": "TAA", "forecast": "Forecast", "syn": "SYN"}

_LB_COLS = ["Model", "Provider"] + [_TASK_LABELS[t] for t in _TASK_ORDER] + ["Overall"]
_MODEL_COLS = ["Task", "Score", "Items", "Scored", "Completed", "AUC"]
_RESULT_COLS = ["Item", "Score", "Correct", "Pre-Cutoff", "Breakdown"]

# ---------------------------------------------------------------------------
# Task explainer content
# ---------------------------------------------------------------------------

_TASK_EXPLAINERS = {
    "rcm": {
        "title": "RCM — Root Cause Mapping",
        "description": (
            "Given a CVE description, the model must identify the underlying weakness class(es) "
            "using the CWE (Common Weakness Enumeration) taxonomy. "
            "This tests whether a model understands *why* a vulnerability exists, not just what it does."
        ),
        "prompt_note": (
            "Prompt: *You are a vulnerability analyst. Given the CVE description below, identify the most "
            "appropriate CWE identifier(s). Respond with only the CWE id(s) in the form CWE-XXX.*"
        ),
        "example_input": (
            "CVE-2024-23897: Jenkins 2.441 and earlier, LTS 2.426.2 and earlier does not disable a feature "
            "of its CLI command parser that replaces an '@' character followed by a file path in an argument "
            "with the file's contents, allowing unauthenticated attackers to read arbitrary files on the "
            "Jenkins controller file system."
        ),
        "example_label": "CWE-22, CWE-88",
        "example_response_good": "CWE-22",
        "example_response_bad": "CWE-200",
        "scoring": (
            "**Set F1** — precision and recall over the predicted vs. true CWE set. "
            "Partial credit when a model gets one CWE right out of two. "
            "Score is prequentially aggregated with a 0.99 fading factor so recent items count more."
        ),
    },
    "vsp": {
        "title": "VSP — CVSS Score Prediction",
        "description": (
            "Given a CVE description, the model must predict the full CVSS v3.1 base vector string. "
            "This tests calibrated severity reasoning across eight independent metrics: "
            "attack vector, complexity, privileges required, user interaction, scope, "
            "and confidentiality/integrity/availability impact."
        ),
        "prompt_note": (
            "Prompt: *You are a vulnerability analyst. Given the CVE description below, predict the CVSS "
            "v3.1 base vector string. Respond with only the CVSS v3.1 vector, e.g. "
            "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H.*"
        ),
        "example_input": (
            "CVE-2024-6387: A race condition in OpenSSH's server (sshd) in glibc-based Linux systems "
            "allows unauthenticated remote code execution as root due to a signal handler that calls "
            "async-signal-unsafe functions."
        ),
        "example_label": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "example_response_good": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "example_response_bad": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "scoring": (
            "**Normalised MAD** — mean absolute deviation per metric, scaled so that a perfect prediction "
            "scores 1.0 and maximum error scores 0.0. Severity band agreement and component-level "
            "partial credit are also applied. Models that get AC:L vs AC:H wrong (a common mistake on "
            "regreSSHion-class bugs) take a meaningful hit."
        ),
    },
    "ate": {
        "title": "ATE — ATT&CK Technique Extraction",
        "description": (
            "Given an excerpt from a threat intelligence report (CISA advisory, NCSC alert, DFIR write-up, etc.), "
            "the model must identify the MITRE ATT&CK techniques described. "
            "This tests structured threat-behaviour recognition against a living, versioned taxonomy."
        ),
        "prompt_note": (
            "Prompt: *You are a threat-intelligence analyst. Identify the MITRE ATT&CK techniques described "
            "in the report excerpt below. Respond with only the ATT&CK technique ids (e.g. T1059), "
            "comma-separated.*"
        ),
        "example_input": (
            "The threat actor gained initial access via spear-phishing emails containing a malicious LNK file. "
            "After execution, a PowerShell script downloaded a Cobalt Strike beacon from a compromised web server. "
            "Credentials were harvested using a memory-scraping tool, and lateral movement was achieved "
            "via pass-the-hash. Data was staged in a ZIP archive before exfiltration over HTTPS."
        ),
        "example_label": "T1566, T1059, T1105, T1003, T1550, T1560, T1041",
        "example_response_good": "T1566, T1059, T1003, T1550, T1560, T1041",
        "example_response_bad": "T1566, T1059",
        "scoring": (
            "**Set F1** over predicted vs. true technique IDs. Revoked techniques are resolved to their "
            "successor before scoring, so a model citing a deprecated ID is not penalised if the concept "
            "is correct. Sub-technique IDs (e.g. T1059.001) are normalised to the parent."
        ),
    },
    "taa": {
        "title": "TAA — Threat Actor Attribution",
        "description": (
            "Given an intrusion narrative from a public advisory or report, the model must name the most "
            "likely responsible threat actor. This tests attribution reasoning — matching TTPs, targeting, "
            "and infrastructure clues to a known group — and handles the challenge that the same group "
            "is tracked under dozens of different names across vendors."
        ),
        "prompt_note": (
            "Prompt: *You are a threat-intelligence analyst. Based on the intrusion narrative below, name "
            "the most likely threat actor responsible. Respond with only the threat-actor name.*"
        ),
        "example_input": (
            "The intrusion targeted South Korean cryptocurrency exchanges and defence contractors. "
            "The attackers used trojanised cryptocurrency trading software delivered via LinkedIn recruiter messages. "
            "Malware included a custom backdoor communicating over TLS with rotating C2 infrastructure, "
            "and the operation involved significant cryptocurrency theft routed through mixing services."
        ),
        "example_label": "Lazarus Group",
        "example_response_good": "Lazarus Group",
        "example_response_bad": "APT28",
        "scoring": (
            "**Synonym-graph credit**: canonical name = 1.0, known alias (e.g. *Hidden Cobra*, *ZINC*) = 0.5, "
            "related group = 0.0. The alias graph is built from MISP galaxy threat-actor data, "
            "so vendor-specific names are correctly mapped. A model that says 'Hidden Cobra' when "
            "the label is 'Lazarus Group' scores 0.5, not 0."
        ),
    },
    "forecast": {
        "title": "Forecast — Exploitation Probability",
        "description": (
            "Given a CVE description at publication time, the model must estimate the probability "
            "that the vulnerability will be actively exploited in the wild. "
            "Labels are resolved 14+ days later against the CISA Known Exploited Vulnerabilities (KEV) catalogue, "
            "making this a genuine prospective prediction task that cannot be gamed by memorising KEV entries."
        ),
        "prompt_note": (
            "Prompt: *You are a vulnerability prioritisation analyst. Given the CVE description below, "
            "estimate the probability that this vulnerability will be exploited in the wild. "
            "Respond with only a probability between 0 and 1 (e.g. 0.75).*"
        ),
        "example_input": (
            "CVE-2024-21762: A out-of-bounds write vulnerability in Fortinet FortiOS SSL VPN "
            "may allow a remote unauthenticated attacker to execute arbitrary code or command "
            "via specially crafted HTTP requests."
        ),
        "example_label": "exploited=True (later confirmed in CISA KEV)",
        "example_response_good": "0.90",
        "example_response_bad": "0.10",
        "scoring": (
            "**Brier score** (lower is better, inverted to [0,1]) measures calibration of the probability "
            "estimate against the binary KEV outcome. **AUC** measures discrimination across all Forecast "
            "items in a run. Items are withheld for 14 days after creation to prevent KEV leakage "
            "into the label, and the newest 14-day slice is always held out from scoring."
        ),
    },
    "syn": {
        "title": "SYN — Threat Assessment Synthesis",
        "description": (
            "Given a bundle of raw CTI inputs (CVE details, observed TTPs, actor indicators, affected sectors), "
            "the model must produce a coherent, calibrated threat assessment. "
            "This is the most open-ended task: it tests whether a model can integrate heterogeneous "
            "evidence into actionable intelligence without hallucinating facts or over-claiming certainty."
        ),
        "prompt_note": (
            "Prompt: *You are a CTI analyst. From the raw inputs below, produce a concise threat assessment: "
            "name the most likely threat actor, relevant CVEs, MITRE ATT&CK techniques, targeted sectors, "
            "indicators of compromise, and recommended mitigations. Hedge explicitly where the evidence is weak.*"
        ),
        "example_input": (
            "CVE-2024-3400 (CVSS 10.0) exploited in Palo Alto Networks GlobalProtect. "
            "Observed TTPs: T1190 (Exploit Public-Facing Application), T1059.006 (Python), T1105 (Ingress Tool Transfer). "
            "Targeted: US government, critical infrastructure. "
            "IOCs: 144.172.79[.]92, update.pkgs-cloud[.]com. "
            "CISA emergency directive issued."
        ),
        "example_label": "Claims: [actor=UTA0218, cve=CVE-2024-3400, techniques={T1190,T1059,T1105}, sectors={government,critical_infrastructure}, mitigation=patch_immediately]",
        "example_response_good": (
            "Based on the targeting profile and TTPs, this activity is attributed with moderate confidence to "
            "UTA0218, a likely nation-state actor. CVE-2024-3400 enables unauthenticated RCE and is being "
            "actively exploited. Recommended actions: apply PAN-OS hotfix immediately, block listed IOCs, "
            "audit GlobalProtect logs for T1190 indicators. Note: actor attribution remains preliminary "
            "pending further forensic analysis."
        ),
        "example_response_bad": (
            "This is a critical vulnerability exploited by Lazarus Group targeting all sectors globally. "
            "The attack uses T1059, T1190, T1003, T1078, T1071, T1486. Patch everything immediately. "
            "All systems are at risk."
        ),
        "scoring": (
            "**Claim-set scoring** via LLM judge: the reference assessment is decomposed into atomic claims, "
            "then the model response is evaluated for (1) **recall** — what fraction of true claims are covered, "
            "(2) **faithfulness** — no hallucinated facts, (3) **calibration** — appropriate hedging where "
            "evidence is weak. Final score = recall × faithfulness × calibration."
        ),
    },
}


def _task_explainer_md(task: str) -> str:
    e = _TASK_EXPLAINERS[task]
    return f"""
{e['description']}

_{e['prompt_note']}_

---

**Example input**
```
{e['example_input']}
```

**Ground-truth label**
```
{e['example_label']}
```

**Strong model response**
```
{e['example_response_good']}
```

**Weak model response**
```
{e['example_response_bad']}
```

---

**Scoring** — {e['scoring']}
"""


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------

def build_leaderboard_df() -> pd.DataFrame:
    empty = pd.DataFrame(columns=_LB_COLS)
    rows = data.get_leaderboard()
    if not rows:
        return empty
    result = []
    for m in rows:
        row: dict = {"Model": m["model_name"], "Provider": m["provider"]}
        for task in _TASK_ORDER:
            label = _TASK_LABELS[task]
            score = m["task_scores"].get(task)
            row[label] = f"{score:.1%}" if score is not None else "—"
        overall = m.get("overall")
        row["Overall"] = f"{overall:.1%}" if overall is not None else "—"
        result.append(row)
    return pd.DataFrame(result, columns=_LB_COLS)


def build_model_detail(model_id: str) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    empty_tasks = pd.DataFrame(columns=_MODEL_COLS)
    empty_results = pd.DataFrame(columns=_RESULT_COLS)
    if not model_id:
        return empty_tasks, [], empty_results
    runs = data.get_model_runs(model_id)
    if not runs:
        return empty_tasks, [], empty_results
    rows = []
    run_ids: list[str] = []
    for r in runs:
        score = r.get("prequential_score")
        auc = r.get("auc")
        rows.append({
            "Task": _TASK_LABELS.get(r["task"], r["task"]),
            "Score": f"{score:.1%}" if score is not None else "—",
            "Items": r.get("item_count", 0),
            "Scored": r.get("scored_count", 0),
            "Completed": r.get("completed_at") or "—",
            "AUC": f"{auc:.3f}" if auc is not None else "—",
        })
        run_ids.append(r["run_id"])
    return pd.DataFrame(rows, columns=_MODEL_COLS), run_ids, empty_results


def build_results_df(run_id: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=_RESULT_COLS)
    if not run_id:
        return empty
    results = data.get_run_results(run_id)
    if not results:
        return empty
    rows = []
    for r in results:
        score = r.get("score")
        correct = r.get("correct")
        pre_cutoff = r.get("pre_cutoff")
        rows.append({
            "Item": str(r["item_id"])[:8],
            "Score": f"{score:.3f}" if score is not None else "—",
            "Correct": "✓" if correct is True else ("✗" if correct is False else "—"),
            "Pre-Cutoff": "yes" if pre_cutoff is True else ("no" if pre_cutoff is False else "—"),
            "Breakdown": r.get("score_summary", ""),
        })
    return pd.DataFrame(rows, columns=_RESULT_COLS)


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(title="Glokta CTI Leaderboard", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            f"""
            # Glokta — CTI Benchmark Leaderboard
            Cyber-threat-intelligence living benchmark · data from `{data.HF_CTI_DATASET_REPO or "HF_CTI_DATASET_REPO not set"}`
            """
        )

        model_name_to_id = gr.State(value={})

        with gr.Tabs() as tabs:

            # ----------------------------------------------------------------
            # Tab 1: CTI Leaderboard
            # ----------------------------------------------------------------
            with gr.Tab("CTI Leaderboard", id="cti_leaderboard"):
                gr.Markdown(
                    "Prequential (fading-factor weighted) accuracy per task. "
                    "**Click a row to drill into CTI Model Detail.**"
                )
                with gr.Row():
                    refresh_btn = gr.Button("Refresh Data from HF", variant="primary")

                leaderboard_table = gr.Dataframe(
                    label="CTI Leaderboard",
                    interactive=False,
                    wrap=True,
                )

            # ----------------------------------------------------------------
            # Tab 2: CTI Model Detail
            # ----------------------------------------------------------------
            with gr.Tab("CTI Model Detail", id="cti_model_detail") as model_detail_tab:
                gr.Markdown(
                    "Select a model to see its latest prequential score per task. "
                    "**Click a task row to inspect individual item results.**"
                )
                model_dropdown = gr.Dropdown(
                    label="Model",
                    choices=[],
                    value=None,
                    interactive=True,
                )
                model_table = gr.Dataframe(
                    label="Task Scores (click a row to see results)",
                    interactive=False,
                    wrap=True,
                )
                gr.Markdown("### Item Results")
                result_table = gr.Dataframe(
                    label="Per-Item Results",
                    interactive=False,
                    wrap=True,
                )

            # ----------------------------------------------------------------
            # Tab 3: Task Guide
            # ----------------------------------------------------------------
            with gr.Tab("Task Guide", id="task_guide"):
                gr.Markdown("""
## What is the CTI Benchmark?

The Glokta CTI benchmark is a **living, temporally-anchored** evaluation suite for
cyber-threat-intelligence reasoning. Items are derived from public sources (NVD, CISA advisories,
MITRE ATT&CK, MISP galaxy, DFIR reports) and anchored to a `first_available_date` — the date
the information was publicly known. Models are only tested on items published **after** their
training cutoff, preventing label memorisation and making scores a genuine measure of reasoning
rather than recall.

Scores are aggregated using **prequential (fading-factor) accuracy** with a 0.99 decay weight,
so recent items count slightly more than older ones. This keeps the leaderboard sensitive to
model updates and knowledge cutoff improvements.

The benchmark comprises six tasks spanning the full CTI workflow:
""")

                for task in _TASK_ORDER:
                    e = _TASK_EXPLAINERS[task]
                    with gr.Accordion(e["title"], open=False):
                        gr.Markdown(_task_explainer_md(task))

                gr.Markdown("""
---

## Further Reading

- [Glokta on GitHub](https://github.com/JakeBx/Glokta) — self-hosting instructions, architecture overview, and contribution guide
""")

        # State: run_ids parallel to model_table rows
        task_run_ids = gr.State(value=[])

        # --------------------------------------------------------------------
        # Event handlers
        # --------------------------------------------------------------------

        def on_load():
            try:
                data.load_data()
            except Exception as exc:
                print(f"[app] Data load failed: {exc}")

            lb_df = build_leaderboard_df()
            models = [(m["name"], m["id"]) for m in data.get_models()]
            name_to_id = {m["name"]: m["id"] for m in data.get_models()}
            return (
                lb_df,
                name_to_id,
                gr.update(choices=models, value=None),
            )

        def on_refresh():
            try:
                data.load_data()
            except Exception as exc:
                print(f"[app] Reload failed: {exc}")
            lb_df = build_leaderboard_df()
            models = [(m["name"], m["id"]) for m in data.get_models()]
            name_to_id = {m["name"]: m["id"] for m in data.get_models()}
            return (
                lb_df,
                name_to_id,
                gr.update(choices=models, value=None),
            )

        def on_lb_row_click(evt: gr.SelectData, lb_df: pd.DataFrame, name_to_id: dict):
            try:
                model_name = str(lb_df.iloc[evt.index[0]]["Model"])
            except Exception:
                return gr.update(), gr.update()
            model_id = name_to_id.get(model_name, "")
            return (
                gr.update(value=model_id),
                gr.update(selected="cti_model_detail"),
            )

        def on_model_change(model_id: str | None):
            task_df, run_ids, result_df = build_model_detail(model_id or "")
            return task_df, run_ids, result_df

        def on_task_select(evt: gr.SelectData, run_ids: list[str]):
            try:
                run_id = run_ids[evt.index[0]]
            except (IndexError, TypeError):
                return pd.DataFrame(columns=_RESULT_COLS)
            return build_results_df(run_id)

        # --------------------------------------------------------------------
        # Wire events
        # --------------------------------------------------------------------

        demo.load(
            fn=on_load,
            inputs=None,
            outputs=[leaderboard_table, model_name_to_id, model_dropdown],
        )

        refresh_btn.click(
            fn=on_refresh,
            inputs=None,
            outputs=[leaderboard_table, model_name_to_id, model_dropdown],
        )

        leaderboard_table.select(
            fn=on_lb_row_click,
            inputs=[leaderboard_table, model_name_to_id],
            outputs=[model_dropdown, tabs],
        )

        model_dropdown.change(
            fn=on_model_change,
            inputs=[model_dropdown],
            outputs=[model_table, task_run_ids, result_table],
        )
        model_detail_tab.select(
            fn=on_model_change,
            inputs=[model_dropdown],
            outputs=[model_table, task_run_ids, result_table],
        )
        model_table.select(
            fn=on_task_select,
            inputs=[task_run_ids],
            outputs=[result_table],
        )

    return demo


if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7860))
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=port, show_api=False)
