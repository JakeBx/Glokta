"""CTI task registry for the living benchmark.

Mirrors domain/risks.py RISK_DEFINITIONS: a single source of truth describing each
CTI task, its scoring method, ingestion cadence, leak-resistance and whether it is
enabled. The trigger flow queues one cti_run per (active model x enabled task).
"""

from typing import Literal, TypedDict

ScoringType = Literal[
    "set_f1",          # RCM, ATE — set F1 over IDs
    "vector_distance", # VSP — CVSS base-score MAD
    "synonym_graph",   # TAA — alias/related-group BFS (C/P/I credit)
    "brier",           # Forecast — Brier / AUC
    "claim_set",       # SYN — reference-based recall/faithfulness/calibration
]
Cadence = Literal["hourly", "daily", "weekly", "on_release"]
LeakResistance = Literal["low", "moderate", "high"]


class CtiTask(TypedDict):
    label: str
    scoring_type: ScoringType
    cadence: Cadence
    leak_resistance: LeakResistance
    enabled: bool


CTI_TASKS: dict[str, CtiTask] = {
    "rcm": {
        "label": "CVE → CWE classification",
        "scoring_type": "set_f1",
        "cadence": "hourly",
        "leak_resistance": "moderate",
        "enabled": True,
    },
    "vsp": {
        "label": "CVE → CVSS severity prediction",
        "scoring_type": "vector_distance",
        "cadence": "hourly",
        "leak_resistance": "moderate",
        "enabled": True,
    },
    "ate": {
        "label": "Report → ATT&CK technique extraction",
        "scoring_type": "set_f1",
        "cadence": "daily",
        "leak_resistance": "moderate",
        "enabled": True,
    },
    "taa": {
        "label": "Report → threat-actor attribution",
        "scoring_type": "synonym_graph",
        "cadence": "daily",
        "leak_resistance": "low",
        "enabled": True,
    },
    "forecast": {
        "label": "CVE → will-be-exploited forecast",
        "scoring_type": "brier",
        "cadence": "daily",
        "leak_resistance": "high",
        "enabled": True,
    },
    "syn": {
        "label": "CTI analysis & synthesis",
        "scoring_type": "claim_set",
        "cadence": "daily",
        "leak_resistance": "moderate",
        # Enabled: the input-reconstruction gate is enforced at ingest via the hybrid masking
        # policy (ingest_syn_items mask=True + drop-residue), validated in notebook 07.
        "enabled": True,
    },
}

ENABLED_TASKS: list[str] = [k for k, v in CTI_TASKS.items() if v["enabled"]]
