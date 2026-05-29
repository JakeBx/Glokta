"""Risk category definitions for the risk-based leaderboard.

Maps each garak probe category to a human-readable security risk label and
controls which categories are enabled by default in the UI.
"""

from typing import TypedDict


class RiskDefinition(TypedDict):
    label: str
    enabled: bool


RISK_DEFINITIONS: dict[str, RiskDefinition] = {
    "ansiescape": {
        "label": "Terminal Manipulation / Log Poisoning",
        "enabled": True,
    },
    "apikey": {
        "label": "Synthetic Credential Generation",
        "enabled": True,
    },
    "av_spam_scanning": {
        "label": "Missing Output Security Controls",
        "enabled": True,
    },
    "exploitation": {
        "label": "Code Injection (SQLi, SSTI, RCE)",
        "enabled": True,
    },
    "fileformats": {
        "label": "RCE via Model Artifacts",
        "enabled": False,  # silently fails with REST generators; no scan data
    },
    "malwaregen": {
        "label": "Weaponization of Code Generation",
        "enabled": True,
    },
    "packagehallucination": {
        "label": "Supply Chain Poisoning",
        "enabled": True,
    },
    "promptinject": {
        "label": "Prompt Injection / String Hijacking",
        "enabled": True,
    },
    "sysprompt_extraction": {
        "label": "Information Disclosure / Reconnaissance",
        "enabled": True,
    },
    "web_injection": {
        "label": "XSS, CSRF, and Data Exfiltration",
        "enabled": True,
    },
}

ACTIVE_RISKS: list[str] = [k for k, v in RISK_DEFINITIONS.items() if v["enabled"]]


def compute_risk_pass_rates(
    probe_results: list[dict],
    included_risks: list[str] | None = None,
) -> tuple[dict[str, float | None], float | None]:
    """Compute per-risk pass rates and overall mean from a list of probe result dicts.

    Each dict must have: probe_category, pass_count, fail_count.

    Returns:
        per_risk: mapping of category → pass_rate (None if no data for that category)
        overall: arithmetic mean of pass rates for all included risks with data
    """
    if included_risks is None:
        included_risks = ACTIVE_RISKS

    category_pass: dict[str, int] = {}
    category_total: dict[str, int] = {}

    for pr in probe_results:
        cat = pr["probe_category"]
        if cat not in included_risks:
            continue
        p = pr["pass_count"]
        f = pr["fail_count"]
        category_pass[cat] = category_pass.get(cat, 0) + p
        category_total[cat] = category_total.get(cat, 0) + p + f

    per_risk: dict[str, float | None] = {}
    for risk in included_risks:
        total = category_total.get(risk, 0)
        if total > 0:
            per_risk[risk] = category_pass[risk] / total
        else:
            per_risk[risk] = None

    rates_with_data = [r for r in per_risk.values() if r is not None]
    overall = sum(rates_with_data) / len(rates_with_data) if rates_with_data else None

    return per_risk, overall
