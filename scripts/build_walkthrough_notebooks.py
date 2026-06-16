#!/usr/bin/env python3
"""Generate the CTI walkthrough notebooks under docs/walkthrough/.

One notebook per CTI test, each walking through dataflow -> tasking -> scoring with a handful
of in-memory data examples and (optionally live) model calls. Notebooks are DB-free and use the
project's real pure functions (normalise_*, build_prompt, evaluate_item, score_*). Live model
calls go to the HF Llama model when HF_TOKEN is present; otherwise a canned response is used so
the notebook still runs offline.

Run:  PYTHONPATH=src conda run -n glokta python scripts/build_walkthrough_notebooks.py
"""

import json
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "walkthrough")


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": text.strip("\n").splitlines(keepends=True),
    }


def notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3 (glokta)", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


# --- shared setup cell (path resolution, .env load, live/canned model helper) ---------------

SETUP = '''
import os, sys, json

def _find_root(start):
    d = os.path.abspath(start)
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "src", "glokta")):
            return d
        d = os.path.dirname(d)
    raise RuntimeError("could not locate repo root (a dir containing src/glokta)")

ROOT = _find_root(os.getcwd())
sys.path.insert(0, os.path.join(ROOT, "src"))

# Best-effort load of the repo .env so live HF calls have HF_TOKEN; no dotenv dependency.
_envp = os.path.join(ROOT, ".env")
if os.path.exists(_envp):
    for _line in open(_envp):
        _s = _line.strip()
        if _s and not _s.startswith("#") and "=" in _s:
            _k, _v = _s.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
os.environ.setdefault("TESTING", "1")  # relax settings validators if .env is absent

MODEL = "huggingface/meta-llama/Llama-3.1-8B-Instruct"
LIVE = bool(os.environ.get("HF_TOKEN"))
print("repo root :", ROOT)
print("model     :", MODEL)
print("LIVE calls:", LIVE, "(set HF_TOKEN to enable real inference)")

def run_model(prompt, canned, max_tokens=256):
    """Call the model live if HF_TOKEN is set, else return a canned example response."""
    if LIVE:
        from glokta.infrastructure.cti.inference import complete
        try:
            return complete(MODEL, prompt, max_tokens=max_tokens, timeout=60.0, max_retries=1)
        except Exception as exc:
            print("[live call failed -> canned]", type(exc).__name__, str(exc)[:80])
            return canned
    print("[offline -> canned response]")
    return canned
'''

# --- sample data reused across notebooks -----------------------------------------------------

CVE_SAMPLE = '''
# A CVE JSON 5.0 record (cvelistV5 shape). In production these come from the delta feed
# (fetch_recent_cve_records); here we use a representative in-memory example.
CVE_RECORD = {
    "cveMetadata": {"cveId": "CVE-2024-12345", "datePublished": "2024-05-01T10:00:00.000Z",
                    "dateUpdated": "2024-05-20T10:00:00.000Z", "state": "PUBLISHED"},
    "containers": {
        "cna": {
            "descriptions": [{"lang": "en",
                "value": "A SQL injection vulnerability in Acme Portal allows a remote "
                         "unauthenticated attacker to execute arbitrary SQL via the search parameter."}],
            "problemTypes": [{"descriptions": [{"lang": "en", "cweId": "CWE-89",
                              "description": "SQL Injection"}]}],
            "metrics": [{"cvssV3_1": {"vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                                       "baseScore": 9.8}}],
        },
        # CISA-ADP (Vulnrichment) container — here it agrees with the CNA on the CWE.
        "adp": [{"providerMetadata": {"shortName": "CISA-ADP", "dateUpdated": "2024-05-20T10:00:00.000Z"},
                 "problemTypes": [{"descriptions": [{"lang": "en", "cweId": "CWE-89"}]}]}],
    },
}
'''

ADVISORY_SAMPLE = '''
# A parsed CISA advisory (the shape parse_advisory produces). In production this is built from
# the CISA RSS feed + page text; here we use a clean in-memory example.
ADVISORY = {
    "id": "AA24-100A",
    "published": __import__("datetime").date(2024, 4, 9),
    "actor": "APT29",
    "cves": ["CVE-2024-12345"],
    "techniques": ["T1059", "T1566"],
    "text": ("APT29 conducted an espionage campaign attributed with high confidence to the "
             "Russian SVR, exploiting CVE-2024-12345 and using T1059 and T1566."),
    "sections": {
        "Summary": "APT29 attribution and high-confidence assessment (a CONCLUSION).",
        "Technical Details": "The actors used T1059 and T1566; beaconing was observed to 198.51.100.23.",
        "Indicators of Compromise": "198.51.100.23",
        "MITRE ATT&CK Techniques": "T1059, T1566 (a CONCLUSION/mapping)",
        "Mitigations": "Apply vendor patches and enforce MFA.",
    },
}

# Small in-memory reference indices. In production these come from the reference tables via
# build_technique_index() and build_taa_indices(); inline here to keep the notebook DB-free.
TECHNIQUE_INDEX = {"T1059": "T1059", "T1566": "T1566", "T1064": "T1059"}  # T1064 is revoked -> T1059
ALIAS_INDEX = {"apt29": "APT29", "cozy bear": "APT29", "the dukes": "APT29",
               "apt28": "APT28", "fancy bear": "APT28"}
RELATED_INDEX = {"APT29": {"APT28"}, "APT28": {"APT29"}}
'''


# --- per-notebook definitions ----------------------------------------------------------------

def rcm_nb():
    return notebook([
        md("""
# CTI-RCM walkthrough — CVE description → CWE

**Task:** read a CVE description and name the weakness type (CWE id).
**Scoring:** set F1 over CWE ids. **Cadence:** hourly. **Leak-resistance:** moderate.

This notebook walks the three stages — **dataflow → tasking → scoring** — over a couple of
examples using the project's real functions.
"""),
        code(SETUP),
        md("## 1. Dataflow — raw CVE record → normalised RCM item\n`normalise_cve_record` extracts the English description and the CWE label, applying CNA→ADP→NVD provenance."),
        code(CVE_SAMPLE),
        code("""
from glokta.infrastructure.cti.connectors.cve import normalise_cve_record

items = normalise_cve_record(CVE_RECORD, source_revision="walkthrough")
rcm = next(i for i in items if i.task == "rcm")
print("external_id        :", rcm.external_id)
print("input_text         :", rcm.input_text[:90], "...")
print("label (ground truth):", rcm.label)
print("label_provenance   :", rcm.label_provenance)
print("authority_agreement:", rcm.authority_agreement)
print("input_date         :", rcm.input_date, "| label_date:", rcm.label_date)
"""),
        md("## 2. Tasking — build the prompt and call the model\n`build_prompt` renders the RCM template; `run_model` calls the model (live or canned)."),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt, parse_response

prompt = build_prompt("rcm", rcm.input_text)
print(prompt)
print("-" * 70)
response = run_model(prompt, canned="The flaw is SQL injection.\\nAnswer: CWE-89")
print("model response:", repr(response))
print("parsed CWEs   :", parse_response("rcm", response))
"""),
        md("## 3. Scoring — compare prediction to the label\n`evaluate_item` parses + scores in one step (set F1)."),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item

scored = evaluate_item("rcm", rcm.label, response)
print("score    :", scored.score)
print("correct  :", scored.correct)
print("breakdown:", scored.breakdown)
print("parsed   :", scored.parsed_output)
"""),
        md("## 4. A handful of examples\nScore several model answers to see partial credit (set F1)."),
        code("""
examples = {
    "exact":        "Answer: CWE-89",
    "extra guess":  "Could be CWE-89 or CWE-79\\nAnswer: CWE-89, CWE-79",
    "wrong":        "Answer: CWE-22",
    "no answer":    "I cannot determine the weakness.",
}
for label, resp in examples.items():
    s = evaluate_item("rcm", rcm.label, resp)
    print(f"{label:12} score={s.score:.3f} correct={s.correct} pred={s.parsed_output['cwe']}")
"""),
        md("**Takeaway:** RCM is a clean objective task — exact CWE match scores 1.0, an extra wrong guess lowers precision (F1), a miss scores 0.0. Provenance (`authority_agreement`) records CNA/ADP/NVD agreement as a difficulty stratum."),
    ])


def vsp_nb():
    return notebook([
        md("""
# CTI-VSP walkthrough — CVE description → CVSS vector

**Task:** predict the CVSS v3.1 base vector for a CVE.
**Scoring:** `1 − MAD/7.7` over base scores (athenabench), plus severity-band agreement and
component distance. **Cadence:** hourly.
"""),
        code(SETUP),
        md("## 1. Dataflow — the VSP item carries the CVSS label"),
        code(CVE_SAMPLE),
        code("""
from glokta.infrastructure.cti.connectors.cve import normalise_cve_record

vsp = next(i for i in normalise_cve_record(CVE_RECORD, "walkthrough") if i.task == "vsp")
print("input_text:", vsp.input_text[:90], "...")
print("label     :", vsp.label)   # {'vector': 'CVSS:3.1/...', 'base_score': 9.8}
"""),
        md("## 2. Tasking — prompt + model call"),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt, parse_response

prompt = build_prompt("vsp", vsp.input_text)
print(prompt)
print("-" * 70)
response = run_model(prompt, canned="Answer: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
print("model response:", repr(response))
print("parsed vector :", parse_response("vsp", response))
"""),
        md("## 3. Scoring — base-score MAD + severity + component distance"),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item
from glokta.domain.cti.scoring import score_vsp

scored = evaluate_item("vsp", vsp.label, response)
print("score (1 - MAD/7.7):", scored.score)
print("correct (severity match):", scored.correct)
print("breakdown:", scored.breakdown)
"""),
        md("## 4. A handful of examples\nFrom exact to a low-severity guess — watch MAD and severity_match move."),
        code("""
candidates = {
    "exact":        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "near (AV:L)":  "CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "low severity": "CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:L",
    "unparseable":  "I am not sure of the score.",
}
for name, vec in candidates.items():
    s, bd = score_vsp(vec if vec.startswith('CVSS') else None, vsp.label['vector'])
    print(f"{name:13} score={s:.3f} mad={bd['mad']:.2f} sev_match={bd['severity_match']} comp_dist={bd['component_distance']:.3f}")
"""),
        md("**Takeaway:** VSP rewards getting the *severity right* even when the exact vector differs — MAD shrinks as the predicted base score nears the label, and `component_distance` shows how many base metrics were wrong."),
    ])


def ate_nb():
    return notebook([
        md("""
# CTI-ATE walkthrough — report → ATT&CK techniques

**Task:** extract the MITRE ATT&CK techniques described in a report.
**Scoring:** set F1 over technique ids, with revoked ids normalised to their current id.
**Cadence:** daily.
"""),
        code(SETUP),
        md("## 1. Dataflow — advisory → ATE item; reference table normalises revoked ids\n`normalise_report` builds the item; the `TECHNIQUE_INDEX` (from the ATT&CK reference table) maps revoked → current."),
        code(ADVISORY_SAMPLE),
        code("""
from glokta.infrastructure.cti.connectors.report import normalise_report

ate = next(i for i in normalise_report(ADVISORY) if i.task == "ate")
print("input_text:", ate.input_text)
print("label     :", ate.label)            # {'techniques': ['T1059', 'T1566']}
print("technique index (revoked->current):", TECHNIQUE_INDEX)
"""),
        md("## 2. Tasking — prompt + model call"),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt, parse_response

prompt = build_prompt("ate", ate.input_text)
print(prompt)
print("-" * 70)
response = run_model(prompt, canned="Techniques observed: T1059, T1566.")
print("model response :", repr(response))
print("parsed techniques:", parse_response("ate", response))
"""),
        md("## 3. Scoring — set F1 with revoked normalisation\nPass the `technique_index` via `context` so revoked ids (e.g. T1064 → T1059) still match."),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item

ctx = {"technique_index": TECHNIQUE_INDEX}
for name, resp in {
    "exact":          "T1059, T1566",
    "revoked id":     "The actor used T1064 and T1566.",   # T1064 normalises to T1059
    "one missing":    "Only T1059 was seen.",
    "extra wrong":    "T1059, T1566, T1003",
}.items():
    s = evaluate_item("ate", ate.label, resp, ctx)
    print(f"{name:12} score={s.score:.3f} correct={s.correct} pred={s.parsed_output['techniques']}")
"""),
        md("**Takeaway:** ATE is set F1 like RCM, but the ATT&CK reference table lets a *revoked* technique id still count as correct by normalising it to the current id before scoring."),
    ])


def taa_nb():
    return notebook([
        md("""
# CTI-TAA walkthrough — narrative → threat actor

**Task:** attribute an intrusion narrative to a threat actor.
**Scoring:** synonym-aware **C/P/I** credit (athenabench): 1.0 alias match, 0.5 related group,
0.0 otherwise — using the MISP-galaxy alias + related-group graph. **Cadence:** daily.
"""),
        code(SETUP),
        md("## 1. Dataflow — advisory → TAA item; galaxy graph resolves synonyms\nThe label actor is canonicalised; the model's answer is matched against aliases and related groups."),
        code(ADVISORY_SAMPLE),
        code("""
from glokta.infrastructure.cti.connectors.report import normalise_report

taa = next(i for i in normalise_report(ADVISORY) if i.task == "taa")
print("input_text:", taa.input_text)
print("label     :", taa.label)        # {'actor': 'APT29'}
print("alias graph (sample):", {k: ALIAS_INDEX[k] for k in list(ALIAS_INDEX)[:3]})
print("related graph       :", RELATED_INDEX)
"""),
        md("## 2. Tasking — prompt + model call"),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt, parse_response

prompt = build_prompt("taa", taa.input_text)
print(prompt)
print("-" * 70)
response = run_model(prompt, canned="Answer: Cozy Bear")
print("model response:", repr(response))
print("parsed actor  :", parse_response("taa", response))
"""),
        md("## 3. Scoring — C / P / I credit\nAn alias of the right actor is **Correct** (1.0); a *related* group is **Plausible** (0.5)."),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item

ctx = {"alias_index": ALIAS_INDEX, "related_index": RELATED_INDEX}
for name, resp in {
    "alias (C)":     "Answer: Cozy Bear",   # alias of APT29 -> 1.0
    "exact (C)":     "APT29",
    "related (P)":   "APT28",               # related group -> 0.5
    "unknown (I)":   "Answer: Lazarus Group",
}.items():
    s = evaluate_item("taa", taa.label, resp, ctx)
    print(f"{name:12} score={s.score:.2f} result={s.breakdown['result']} pred={s.parsed_output['actor']!r}")
"""),
        md("**Takeaway:** TAA is deliberately not exact-match — naming a *synonym* of the right group is full credit, and naming a *related* group earns partial credit, because real attribution is graph-shaped. (Label quality depends on `detect_actor` precision — a known follow-up.)"),
    ])


def forecast_nb():
    return notebook([
        md("""
# Forecast walkthrough — CVE → will-it-be-exploited?

**Task:** at/near publication, estimate the probability a CVE will be exploited in the wild.
**Scoring:** Brier (stored as `1 − Brier`), plus run-level ROC AUC. **Leak-resistance:** HIGH —
the answer doesn't exist at submission time; it's resolved later by CISA KEV.
"""),
        code(SETUP),
        md("## 1. Dataflow — seed at publication, resolve later via KEV\nAn item is seeded with a provisional `exploited=False` label; when the CVE appears in KEV it flips to `True` and the temporal anchor advances to the KEV date."),
        code(CVE_SAMPLE),
        code("""
from datetime import date
# At publication: the input is the CVE description; the label is unknown -> provisional False.
desc = CVE_RECORD["containers"]["cna"]["descriptions"][0]["value"]
label_unresolved = {"exploited": False}
# Later: CISA KEV lists the CVE -> resolve_forecast_labels flips it and advances the anchor.
label_resolved = {"exploited": True}   # anchor would move to max(publication, KEV dateAdded)
print("input (CVE desc):", desc[:90], "...")
print("label at submission :", label_unresolved, "(leak-proof: answer not yet known)")
print("label after KEV     :", label_resolved)
"""),
        md("## 2. Tasking — prompt + model call (a probability)"),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt, parse_response

prompt = build_prompt("forecast", desc)
print(prompt)
print("-" * 70)
response = run_model(prompt, canned="Given network-exploitable RCE, I estimate 0.85")
print("model response :", repr(response))
print("parsed prob    :", parse_response("forecast", response))
"""),
        md("## 3. Scoring — Brier against the resolved outcome\n`evaluate_item` stores `1 − Brier`; `correct` is whether the >0.5 call matched the outcome."),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item

for outcome_name, label in {"exploited (KEV)": label_resolved, "not exploited": label_unresolved}.items():
    for pname, resp in {"confident 0.85": "0.85", "uncertain 0.5": "0.5", "low 0.1": "0.1"}.items():
        s = evaluate_item("forecast", label, resp)
        print(f"{outcome_name:16} {pname:14} score(1-brier)={s.score:.3f} correct={s.correct}")
"""),
        md("""
## 4. Run-level AUC
Per item we store Brier; across a run we also compute ROC AUC (`auc_for_run`). AUC needs both
classes present, so it's `None` for a single-class slice.
"""),
        code("""
from types import SimpleNamespace
from glokta.application.cti.scoring_aggregate import auc_for_run

# Simulate a few scored forecast results (score_breakdown carries prob + outcome).
fake = [SimpleNamespace(score_breakdown={"prob": p, "outcome": o})
        for p, o in [(0.9, 1.0), (0.8, 1.0), (0.2, 0.0), (0.1, 0.0)]]
print("AUC (perfect separation):", auc_for_run(fake))
print("AUC (single class)      :", auc_for_run(fake[:2]))
"""),
        md("**Takeaway:** Forecast is the structurally leak-proof task — a model evaluated at publication cannot have memorised an answer that KEV only assigns weeks later. Advancing the temporal anchor to the KEV date keeps post-cutoff outcomes from being mis-tagged as pre-cutoff."),
    ])


def syn_nb():
    return notebook([
        md("""
# CTI-SYN walkthrough — analysis & synthesis *(gated)*

**Task:** from *reconstructed inputs* (observations only), produce a threat assessment; score it
against the advisory's claim set on **recall**, **faithfulness** (judge-assisted), and
**calibration**. This is the novel contribution — and the hardest. SYN is **enabled** in
production; its input-reconstruction leakage gate is enforced at ingest via the hybrid masking
policy (notebook 07).
"""),
        code(SETUP),
        md("""
## 1. Dataflow — reconstruct inputs, build the label claim set
`reconstruct_inputs` keeps observation sections (Technical Details / IOC / Overview) and **drops
conclusions** (Summary / Attribution / ATT&CK mapping / Mitigations) so the model scores on
analysis, not summarisation. `build_claim_set` builds the label: deterministic actor/CVE/technique/
IOC claims, plus a pinned LLM for sectors/mitigations/hedges (skipped here with `judge_infer=None`).
"""),
        code(ADVISORY_SAMPLE),
        code("""
from glokta.infrastructure.cti.claim_extraction import reconstruct_inputs, build_claim_set

inputs = reconstruct_inputs(ADVISORY)
print("RECONSTRUCTED INPUTS (model sees only this):\\n", inputs)
print("-" * 70)
claim_set = build_claim_set(ADVISORY, judge_infer=None)   # deterministic claims only
label = {"claims": [{"type": c.type, "value": c.value, "hedge_level": c.hedge_level}
                    for c in claim_set.claims]}
print("LABEL CLAIM SET:")
for c in label["claims"]:
    print("  ", c)
"""),
        md("## 2. Tasking — the model writes an assessment from the inputs"),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt

prompt = build_prompt("syn", inputs)
print(prompt)
print("-" * 70)
canned = ("Assessment: This activity is consistent with APT29. The campaign exploited "
          "CVE-2024-12345 using T1059 and T1566, with beaconing to 198.51.100.23. "
          "Targeting likely includes government sectors (hedged).")
response = run_model(prompt, canned=canned, max_tokens=400)
print("model assessment:\\n", response)
"""),
        md("""
## 3. Scoring — recall / faithfulness / calibration
`claim_set_from_text` extracts the model's claims; `score_syn` compares them to the label. The
faithfulness judge checks whether each model claim is grounded in the *inputs* — here we inject a
stub judge (`lambda claim, inputs: True`); in production this is an LLM judge (`make_grounding_judge`).
"""),
        code("""
from glokta.infrastructure.cti.evaluator import evaluate_item

ctx = {"alias_index": ALIAS_INDEX, "inputs": inputs, "judge": (lambda claim, inputs: True)}
scored = evaluate_item("syn", label, response, ctx)
print("primary score:", round(scored.score, 3))
print("breakdown    :", scored.breakdown)
print("model claims :", scored.parsed_output["claims"])
"""),
        md("""
## 4. Why faithfulness matters
A *confidently wrong* CTI claim is worse than a miss, so faithfulness (precision of grounded
claims) is weighted highest. Below, the model hallucinates an extra actor + IOC the inputs don't
support; a discerning judge rejects them and faithfulness drops.
"""),
        code("""
hallucinated = response + " We also attribute this to APT28 and observed 10.0.0.99."

def strict_judge(claim, inputs):
    # ground a claim only if its value literally appears in the inputs
    return str(claim.value).lower() in inputs.lower()

ctx_strict = {"alias_index": ALIAS_INDEX, "inputs": inputs, "judge": strict_judge}
s2 = evaluate_item("syn", label, hallucinated, ctx_strict)
print("with strict judge -> recall:", round(s2.breakdown["recall"], 3),
      "faithfulness:", None if s2.breakdown["faithfulness"] is None else round(s2.breakdown["faithfulness"], 3),
      "calibration:", s2.breakdown["calibration"])
"""),
        md("**Takeaway:** SYN keeps an objective backbone (recall + calibration) and reserves the LLM judge for the genuinely fuzzy faithfulness check. It runs in production with the leakage gate enforced at ingest (masking + drop-residue); `run_syn_pilot` remains a manual spot-check."),
    ])


# --- SYN pilot notebook (real AA advisories + leakage-gate analysis) -------------------------


def syn_pilot_nb():
    return notebook([
        md("""
# SYN pilot — run CTI-SYN against recent CISA advisories + analyse results

SYN is **enabled** in production, and its trustworthiness rests on the *input-reconstruction
leakage gate* enforced at ingest: the model must see only **observations**, never the advisory's
**conclusions** (actor attribution, ATT&CK mapping). This notebook is that gate made transparent.
If those conclusions leak into the
reconstructed inputs, a model can score well just by copying — and SYN becomes a summarisation task.

This notebook is the manual gate in notebook form. It:
1. loads **real AA-series CISA advisories** from the local cache (downloaded by
   `scripts/download_cisa_advisories.py`; falls back to a live index fetch),
2. **rolls in CCCS (Canada) + NCSC (UK) + The DFIR Report** via the multi-source connector
   (with **Malpedia** enriching the actor alias graph) and **dedupes across sources** — joint
   advisories are co-sealed, so the same report arrives from several CERTs and must be collapsed
   to one copy (preferring CISA) before scoring,
3. parses each page into sections with `parse_source_advisory` and builds a SYN item
   (reconstructed inputs + deterministic claim-set label),
4. **measures leakage** — do the synthesised conclusions (actor / technique) appear verbatim in
   the inputs? — the gate's key signal,
5. runs the model and scores recall / faithfulness / calibration,
6. reports a per-advisory table + aggregates and how to read them.

This is the transparent equivalent of `run_syn_pilot` ([syn_service.py](../../src/glokta/application/cti/syn_service.py)).
Source suitability for CCCS/NCSC/ACSC is reviewed in
[08_source_review_national_certs.ipynb](08_source_review_national_certs.ipynb).

> Prereq for live data: run `PYTHONPATH=src python scripts/download_cisa_advisories.py 100`
> once to populate `data/cisa_advisories/`. CCCS/NCSC are fetched live and best-effort (a
> blocked/timed-out source just contributes fewer items).
"""),
        code(SETUP),
        md("## 0. Config\nKnobs (env-overridable so the notebook stays quick)."),
        code("""
MAX_ADVISORIES = int(os.environ.get("SYN_PILOT_MAX", "5"))   # per source
USE_GALAXY = os.environ.get("SYN_PILOT_GALAXY", "1") == "1"  # fetch MISP galaxy for actor claims
MASK = os.environ.get("SYN_PILOT_MASK", "1") == "1"          # hybrid leakage policy: mask + drop
# Which sources to collect. CISA comes from the on-disk cache; CCCS/NCSC/DFIR are fetched live.
SOURCES_TO_USE = [s.strip() for s in os.environ.get("SYN_PILOT_SOURCES", "cisa,cccs,ncsc,dfir").split(",") if s.strip()]
PRIORITY = ("cisa", "cccs", "ncsc", "dfir")                  # dedup keeps the highest-priority copy
MIN_INPUT_CHARS = 200                                        # drop advisories thinner than this
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
CACHE_DIR = os.path.join(ROOT, "data", "cisa_advisories")
# The conclusion claim types that MUST NOT appear in the reconstructed inputs:
LEAK_TYPES = {"actor", "technique"}
print("max/source:", MAX_ADVISORIES, "| sources:", SOURCES_TO_USE, "| galaxy:", USE_GALAXY, "| mask:", MASK)
"""),
        md("## 1. Build the actor alias graph (optional)\nLets the deterministic extractor recognise actor names. Two sources merge into one index: the **MISP galaxy** (bulk) and **Malpedia** (best-effort, capped — its API is slow). Falls back to empty/galaxy-only if a feed is unavailable."),
        code("""
import httpx

def _add_actors(actors):
    for a in actors:
        alias_index[a.canonical_name.lower()] = a.canonical_name
        for al in (a.aliases or []):
            alias_index[al.lower()] = a.canonical_name

alias_index = {}
if USE_GALAXY:
    try:
        from glokta.infrastructure.cti.connectors.galaxy import fetch_galaxy_cluster, normalise_galaxy
        with httpx.Client(timeout=60.0, headers=HEADERS) as c:
            _add_actors(normalise_galaxy(fetch_galaxy_cluster(c)))
        print("alias index after galaxy:", len(alias_index))
    except Exception as exc:
        print("galaxy unavailable -> actor claims limited:", type(exc).__name__, str(exc)[:60])
    # Malpedia enrichment (capped; the API is slow / sometimes blocks automated clients).
    try:
        from glokta.infrastructure.cti.connectors.malpedia import fetch_malpedia_actors, normalise_malpedia_actors
        with httpx.Client(timeout=20.0, headers=HEADERS, follow_redirects=True) as c:
            _add_actors(normalise_malpedia_actors(fetch_malpedia_actors(c, limit=40)))
        print("alias index after malpedia:", len(alias_index))
    except Exception as exc:
        print("malpedia unavailable -> galaxy aliases only:", type(exc).__name__, str(exc)[:60])
"""),
        md("## 2. Dataflow — collect advisories from CISA + CCCS + NCSC + DFIR\nCISA AA pages load from the disk cache (or a live index fetch). CCCS, NCSC and **The DFIR Report** are collected live via the multi-source connector: `fetch_source_index` enumerates each source (the CERTs' listings; DFIR's RSS feed), then `parse_source_advisory` isolates the body, splits by heading, and buckets sections into observations (kept) vs conclusions/chrome (dropped) using that source's drop-keywords. Live fetches are best-effort — a blocked or slow source simply contributes fewer items."),
        code("""
import glob, json
from glokta.infrastructure.cti.connectors.report import (
    fetch_aa_advisory_index, fetch_source_index, parse_advisory_page,
    parse_source_advisory, detect_actor)
from glokta.infrastructure.cti.claim_extraction import _INPUT_SECTIONS

def load_cisa_cache(n):
    files = sorted(glob.glob(os.path.join(CACHE_DIR, "*.html")))
    idx = {}
    if os.path.exists(os.path.join(CACHE_DIR, "index.json")):
        idx = json.load(open(os.path.join(CACHE_DIR, "index.json")))
    out = []
    for f in files[:n]:
        slug = os.path.basename(f)[:-5]
        html = open(f, encoding="utf-8").read()
        adv = parse_advisory_page({"link": idx.get(slug, slug)}, html)
        adv["source_site"] = "cisa"   # tag for cross-source dedup
        out.append(adv)
    return out

def load_cisa_live(n):
    out = []
    with httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True) as c:
        urls = fetch_aa_advisory_index(c, max_pages=(n // 10) + 1, headers=HEADERS)[:n]
        for u in urls:
            try:
                out.append(parse_source_advisory({"link": u}, c.get(u).text, "cisa"))
            except Exception as exc:
                print("  cisa fetch failed:", u, type(exc).__name__)
    return out

def collect_live(source, n):
    out = []
    try:
        with httpx.Client(timeout=20.0, headers=HEADERS, follow_redirects=True) as c:
            urls = fetch_source_index(c, source, max_pages=2, headers=HEADERS)[:n]
            print(f"  {source}: index -> {len(urls)} urls")
            for u in urls:
                try:
                    out.append(parse_source_advisory({"link": u}, c.get(u).text, source))
                except Exception as exc:
                    print(f"    {source} fetch failed:", u, type(exc).__name__)
    except Exception as exc:
        print(f"  {source}: index unavailable ->", type(exc).__name__, str(exc)[:70])
    return out

pool = []
if "cisa" in SOURCES_TO_USE:
    cisa = load_cisa_cache(MAX_ADVISORIES) or load_cisa_live(MAX_ADVISORIES)
    print(f"cisa: {len(cisa)} advisories (cache or live)")
    pool += cisa
for s in [x for x in SOURCES_TO_USE if x != "cisa"]:   # cccs / ncsc / dfir
    got = collect_live(s, MAX_ADVISORIES)
    print(f"{s}: collected {len(got)} advisories")
    pool += got

for adv in pool:
    adv["actor"] = detect_actor(adv["text"], alias_index)

print(f"\\ncollected pool: {len(pool)} advisories across {len({a['source_site'] for a in pool})} source(s)")
for adv in pool:
    kept = [s for s in _INPUT_SECTIONS if (adv.get("sections") or {}).get(s)]
    print(f"  [{adv['source_site']:4}] {adv['id'][:22]:22} body={len(adv['text']):6}c  "
          f"tech={len(adv['techniques'])} cve={len(adv['cves'])} actor={adv['actor']} kept={kept}")
"""),
        md("**Reconstruction coverage** is the first health check: `kept` should contain `Technical Details` (and often `Indicators of Compromise`). Empty kept-sections means that source's section extractor didn't match the page — a per-source finding (see notebook 08)."),
        md("""
## 2b. Dedupe across sources
National CERTs co-seal joint advisories, so the same report arrives from CISA **and** CCCS/NCSC
under different ids. `dedupe_advisories` fingerprints each by its **CVE set** (with a normalised-
title fallback for CVE-less reports) and keeps the highest-priority copy (`cisa > cccs > ncsc`).
This stops the benchmark double-counting an item — and stops it scoring a national copy of an
advisory the model likely trained on in its CISA form.
"""),
        code("""
from glokta.infrastructure.cti.dedupe import dedupe_advisories

res = dedupe_advisories(pool, priority=PRIORITY)
print(f"pool {len(pool)} -> kept {len(res.kept)}  (dropped {len(res.dropped)} duplicate(s))")
if res.dropped:
    print("\\ndropped as duplicates:")
    for adv, kept_id in res.dropped:
        print(f"  [{adv['source_site']:4}] {adv['id'][:28]:28} == kept {kept_id}")
else:
    print("(no cross-source duplicates in this slice — e.g. only one source was reachable)")

advisories = res.kept   # the deduped set flows into the SYN pipeline below
print("\\ndeduped set by source:",
      {s: sum(1 for a in advisories if a['source_site'] == s) for s in PRIORITY})
"""),
        md("""
## 3. Build SYN items — reconstruct, **mask**, drop-residue
The hybrid leakage policy (`MASK=1`):
1. `reconstruct_inputs` keeps observation sections, drops conclusion sections;
2. `mask_conclusions` removes the synthesised *labels* still embedded in the prose — ATT&CK
   technique ids and the actor name/aliases — while keeping the behavioural evidence (masking the
   *id*, not the description, is the legitimate transform that makes SYN a synthesis task);
3. **drop** any advisory that still leaks after masking, or whose masked input is too thin.

The table below shows leakage **before vs. after** masking and the keep/drop decision. If no
advisory reconstructs at all, we fall back to two labelled fixtures.
"""),
        code("""
from datetime import date
from glokta.infrastructure.cti.claim_extraction import reconstruct_inputs, build_claim_set

FIXTURES = [
    {  # CLEAN: actor + techniques live only in dropped (Summary / MITRE) sections
        "id": "AA-DEMO-CLEAN", "published": date(2024, 3, 1), "actor": "APT29",
        "cves": ["CVE-2024-21887"], "techniques": ["T1190", "T1133"],
        "text": "APT29 campaign against network appliances.",
        "sections": {
            "Summary": "CISA attributes this activity to APT29 (Russian SVR).",
            "Technical Details": ("The threat actor exploited an internet-facing appliance and "
                                  "established outbound C2 beaconing to 203.0.113.10 over HTTPS."),
            "Indicators of Compromise": "203.0.113.10",
            "MITRE ATT&CK Techniques": "T1190 Exploit Public-Facing Application; T1133 External Remote Services",
            "Mitigations": "Patch appliances; restrict external remote services.",
        },
    },
    {  # LEAKS: technique T1059 is stated inline in Technical Details (an observation+conclusion blur)
        "id": "AA-DEMO-LEAK", "published": date(2024, 4, 1), "actor": "APT28",
        "cves": ["CVE-2023-23397"], "techniques": ["T1059", "T1566"],
        "text": "APT28 phishing campaign.",
        "sections": {
            "Summary": "Attributed to APT28 (GRU).",
            "Technical Details": ("The actors used T1059 command execution and delivered phishing "
                                  "emails, exploiting CVE-2023-23397; beacons reached 198.51.100.5."),
            "Indicators of Compromise": "198.51.100.5",
            "MITRE ATT&CK Techniques": "T1059; T1566 Phishing",
            "Mitigations": "Apply the vendor patch for CVE-2023-23397.",
        },
    },
]

from glokta.infrastructure.cti.claim_extraction import mask_conclusions

def _leak(inputs, cs):
    concl = [c for c in cs.claims if c.type in LEAK_TYPES]
    leaked = [c for c in concl if str(c.value).lower() in inputs.lower()]
    return len(leaked), len(concl)

source = advisories if any(reconstruct_inputs(a) for a in advisories) else FIXTURES
if source is FIXTURES:
    print("USING DEMO FIXTURES (live data unavailable / not reconstructable)\\n")

syn_items = []   # (advisory, final_inputs, claim_set)
dropped = 0
print(f"{'advisory':12} {'raw_leak':>9} {'masked_leak':>12}  decision")
for adv in source:
    raw = reconstruct_inputs(adv)
    if not raw:
        continue
    cs = build_claim_set(adv, judge_infer=None)  # deterministic claims (no LLM extractor in the pilot)
    rn, rd = _leak(raw, cs)
    if not MASK:
        syn_items.append((adv, raw, cs))
        print(f"{adv['id']:12} {rn:>4}/{rd:<4} {'(masking off)':>12}  kept")
        continue
    masked = mask_conclusions(raw, actor=adv.get("actor"), alias_index=alias_index)
    mn, md_ = _leak(masked, cs)
    if mn > 0 or len(masked) < MIN_INPUT_CHARS:
        dropped += 1
        why = "residual leak" if mn > 0 else "too thin"
        print(f"{adv['id']:12} {rn:>4}/{rd:<4} {mn:>5}/{md_:<6}  DROPPED ({why})")
        continue
    syn_items.append((adv, masked, cs))
    print(f"{adv['id']:12} {rn:>4}/{rd:<4} {mn:>5}/{md_:<6}  kept")

print(f"\\nkept {len(syn_items)} / {len(source)} advisories (dropped {dropped})")
"""),
        md("""
## 4. Confirm the kept set is leak-free
After the mask + drop policy, the kept inputs should contain **none** of their conclusion claims
(actor / technique) — CVEs and IOCs remain, as observations. Below we verify that and show a masked
snippet so you can sanity-check the inputs still read as genuine observations.
"""),
        code("""
for adv, inputs, cs in syn_items:
    leaked, total = _leak(inputs, cs)
    print(f"  {adv['id']:12} residual_leak={leaked}/{total}", "OK" if leaked == 0 else "<-- STILL LEAKING")

if syn_items:
    snippet = syn_items[0][1]
    print("\\nmasked-input snippet (", syn_items[0][0]["id"], "):\\n", snippet[:500], "...")
"""),
        md("## 5. Tasking + scoring — run the model and score each item\nThe faithfulness judge: live runs use the grounding judge (here the same HF model, for the pilot); offline uses a strict substring stub. Production uses `CTI_JUDGE_MODEL` (a stronger model)."),
        code("""
from glokta.infrastructure.cti.prompts import build_prompt
from glokta.infrastructure.cti.evaluator import evaluate_item

if LIVE:
    from glokta.infrastructure.cti.judge import make_grounding_judge
    from glokta.infrastructure.cti.inference import complete
    judge = make_grounding_judge(infer=complete, model=MODEL)  # pilot: model-as-judge
else:
    judge = lambda claim, inputs: str(claim.value).lower() in inputs.lower()

records = []
for adv, inputs, cs in syn_items:
    label = {"claims": [{"type": c.type, "value": c.value, "hedge_level": c.hedge_level}
                        for c in cs.claims]}
    prompt = build_prompt("syn", inputs)
    canned = ("Assessment: likely APT29 activity exploiting CVE-2024-21887 via T1190, "
              "with C2 beaconing to 203.0.113.10. Targeting government sectors (assessed).")
    response = run_model(prompt, canned=canned, max_tokens=400)
    ctx = {"alias_index": alias_index, "inputs": inputs, "judge": judge}
    s = evaluate_item("syn", label, response, ctx)
    concl = [c for c in cs.claims if c.type in LEAK_TYPES]
    leaked = sum(1 for c in concl if str(c.value).lower() in inputs.lower())
    f = s.breakdown["faithfulness"]
    records.append({
        "advisory": adv["id"],
        "input_chars": len(inputs),
        "claims": len(cs.claims),
        "leak_rate": round(leaked / len(concl), 2) if concl else 0.0,
        "recall": round(s.breakdown["recall"], 2),
        "faithfulness": None if f is None else round(f, 2),
        "calibration": s.breakdown["calibration"],
        "primary": round(s.score, 3),
    })
    print("scored", adv["id"])
"""),
        md("## 6. Results analysis"),
        code("""
import pandas as pd
df = pd.DataFrame(records)
if df.empty:
    print("No SYN items were scored — reconstruction produced no inputs (see section coverage in step 2).")
    print("Likely causes:")
    print("  * the recent feed items are KEV-catalog notices, not AA-series advisories, or")
    print("  * the HTML->section extractor didn't match this advisory's heading structure.")
    print("This is the gate working as intended: SYN should NOT be enabled on data it can't reconstruct.")
else:
    print(df.to_string(index=False))
    print()
    print("AGGREGATE (means):")
    print(df[["input_chars", "leak_rate", "recall", "faithfulness", "calibration", "primary"]]
          .mean(numeric_only=True).round(3).to_string())
    print()
    high_leak = df[df["leak_rate"] > 0]
    print(f"advisories with leakage: {len(high_leak)} / {len(df)}")
"""),
        md("""
## 7. How to read this — the gate decision

The hybrid policy is doing the work here:
- **Masking** drives `raw_leak` (often 30–100% on real AA advisories — they inline ATT&CK ids in
  Technical Details) down to **0** on the kept set, by removing the technique-id / actor labels
  while leaving the behavioural evidence. That's the legitimate transform that makes SYN a
  *synthesis* task rather than a copy task.
- **Drop-residue** is the cost: advisories where attribution is woven unmaskably into the prose, or
  that go too thin after masking, are removed. Watch the `kept / dropped` count — if you're
  dropping most of the corpus, masking isn't enough and the section extractor needs work.
- **Low `faithfulness`** = the model asserts claims the inputs don't support (hallucination — the
  CTI-critical failure); this is what the LLM judge is for. **`calibration`** rewards hedging.

> `leak_rate == 0` is now true **by construction** on the kept set, so it's *necessary but not
> sufficient*: pair it with a human spot-check (the masked snippet above) that the inputs still read
> as genuine observations, and verify a model can't trivially recover a masked technique with no
> behavioural evidence present.

**SYN runs in production** because this gate is enforced at ingest: the kept set is leak-free, the
drop rate is acceptable, and masked inputs read naturally. The masking + drop policy lives in
`mask_conclusions` / `build_syn_item` (`mask=True`) — wired through the `cti_ingest_syn` flow via
`ingest_syn_items(..., mask=True, alias_index=...)`. Re-run this notebook to re-validate the gate
when adding a new source.

> Caveat: faithfulness here uses the model-under-test as its own judge (or an offline stub). A real
> gate run should set `CTI_JUDGE_MODEL` to a stronger, independent model.
"""),
        md("## Production equivalent\nOnce SYN items are ingested (`cti_ingest_syn` / `ingest_syn_items`) and a model row exists, the same evaluation runs via the DB-backed entrypoint:\n\n```python\nfrom glokta.application.cti.syn_service import run_syn_pilot\nrun_syn_pilot(db, 'huggingface/meta-llama/Llama-3.1-8B-Instruct', limit=5)\n# then inspect cti_results + each item's input_text vs label\n```"),
    ])


def source_review_nb():
    return notebook([
        md("""
# Source review — national-CERT advisory feeds (ACSC / NCSC / CCCS)

The benchmark's report-driven tasks (ATE, TAA, SYN) currently draw from **CISA's AA-series**
(`fetch_aa_advisory_index` + `parse_advisory_page`). This notebook reviews three national-CERT
feeds as **additional advisory sources** and scores each against what the ingest path actually
needs — *not* against how authoritative they are (all three are top-tier), but against how well
they fit the pipeline.

Candidates:
- **ACSC** (Australia) — `cyber.gov.au/about-us/view-all-content/alerts-and-advisories`
- **NCSC** (UK) — `ncsc.gov.uk/section/keep-up-to-date/reports-advisories`
- **CCCS** (Canada) — `cyber.gc.ca/en/alerts-advisories`

The findings cells below are **captured from a live structural probe** (June 2026); the optional
probe in §3 re-fetches one sample page per source so you can re-verify. The notebook is DB-free.
"""),
        code(SETUP),
        md("""
## 1. What the benchmark needs from a source

Suitability is defined by the existing ingest path, not by editorial quality. A source must give us:

| # | Criterion | Why (which code depends on it) |
|---|-----------|--------------------------------|
| C1 | **Pageable index** | `fetch_aa_advisory_index` walks a listing to enumerate advisory URLs newest-first. |
| C2 | **On-page section structure** | `parse_advisory_page` isolates a body container and splits on h2/h3 headings; PDF-only advisories don't parse. |
| C3 | **Separable conclusion labels** | ATE/TAA/SYN labels = ATT&CK technique-ids, actor attribution, CVEs, IOCs — and `reconstruct_inputs`/`mask_conclusions` need them in their *own* sections so they can be withheld. |
| C4 | **Original (non-joint) content** | Co-sealed joint advisories duplicate the CISA AA corpus (same text, different slug) → contamination + dedup. |
| C5 | **Volume & recency** | Enough recent original items to be worth a connector. |
| C6 | **Fetchability** | Static HTML (cheap `httpx`) vs JS/bot-throttled (needs headless). |
"""),
        md("## 2. Captured findings\nOne sample advisory per source was parsed live to capture its real heading set and label availability. `headings_verified=False` means the page/listing was bot-throttled during this review and the structure is indicative only."),
        code('''
SOURCES = {
    "ACSC": {
        "index_url": "https://www.cyber.gov.au/about-us/view-all-content/alerts-and-advisories?type=329",
        "index_pagination": "JS-rendered facet listing; bot-throttled (httpx timed out repeatedly)",
        "advisory_url_patterns": ["/about-us/view-all-content/alerts-and-advisories/<slug>",
                                  "/about-us/advisories/<slug>"],
        "sample": "apt40-advisory-prc-mss-tradecraft-in-action",
        # Indicative only — the page itself bot-throttled in this review.
        "headings": ["Summary", "Technical details", "Detection and mitigation",
                     "Indicators of compromise", "MITRE ATT&CK"],
        "headings_verified": False,
        "has_attack_ids": True, "has_actor_attribution": True,
        "has_cves": True, "has_iocs": True,
        "content_on_page": "mixed — several advisories are PDF-only (e.g. ACSC-Advisory-2020-008)",
        "joint_overlap": "HIGH — flagship advisories (APT40, Salt Typhoon) are co-sealed with CISA/FBI/NCSC",
        "fetchability": "POOR — listing + pages bot-throttled; needs headless or a facet/JSON API",
    },
    "NCSC": {
        "index_url": "https://www.ncsc.gov.uk/section/keep-up-to-date/reports-advisories",
        "index_pagination": "shallow single list (~6 items), no pager; mixes news posts and advisories",
        "advisory_url_patterns": ["/news/<slug>"],
        "sample": "apt28-exploit-routers-to-enable-dns-hijacking-operations",
        "headings": ["Executive summary", "Introduction", "APT28 malicious DNS activity",
                     "Indicators of compromise", "MITRE ATT&CK®", "Mitigation"],
        "headings_verified": True,
        "has_attack_ids": True, "has_actor_attribution": True,
        "has_cves": True, "has_iocs": True,
        "content_on_page": "yes on flagship advisories; many full CSAs + malware reports are PDF",
        "joint_overlap": "HIGH — most CSAs co-sealed with CISA/allies",
        "fetchability": "OK for /news/<slug> pages; index too shallow to enumerate a corpus",
    },
    "CCCS": {
        "index_url": "https://www.cyber.gc.ca/en/alerts-advisories",
        "index_pagination": "single very large listing page (one fetch enumerates all); bilingual /en/ /fr/",
        "advisory_url_patterns": ["/en/alerts-advisories/<slug>", "/en/alerts/<slug>"],
        "sample": "alphvblackcat-ransomware-targeting-canadian-industries",
        "headings": ["Audience", "Purpose", "Details",
                     "Tactics, techniques, and procedures (TTP)", "Suggested actions",
                     "Indicators of compromise", "References", "MITRE ATT&CK techniques"],
        "headings_verified": True,
        "has_attack_ids": True, "has_actor_attribution": True,
        "has_cves": False,  # ALPHV sample had none; CCCS CVE coverage is inconsistent
        "has_iocs": True,
        "content_on_page": "yes — full text on-page (AL-numbered originals, e.g. AL23-010), no PDF",
        "joint_overlap": "MIXED — original AL-numbered items + republished CERT-FR/CISA items",
        "fetchability": "GOOD per-page; index page is huge (>10MB) but a single fetch",
    },
}
for name, s in SOURCES.items():
    print(f"{name}: verified_headings={s['headings_verified']}  "
          f"attack={s['has_attack_ids']} actor={s['has_actor_attribution']} "
          f"cve={s['has_cves']} ioc={s['has_iocs']}")
'''),
        md("""
### Does our existing CISA section-classifier transfer?

`parse_advisory_page` buckets each heading via `_classify_heading` into **Overview / Technical
Details / Indicators of Compromise** (kept as model input) vs **Dropped** (conclusions + chrome).
Running it over each source's *real* heading set shows what the current rules already handle and
where a source needs tuning before its conclusions are safely withheld.
"""),
        code('''
from glokta.infrastructure.cti.connectors.report import _classify_heading
from glokta.infrastructure.cti.claim_extraction import _INPUT_SECTIONS

print(f"{'source':6} {'heading':42} {'kept?':5} bucket")
print("-" * 78)
for name, s in SOURCES.items():
    for h in s["headings"]:
        keep, bucket = _classify_heading(h)
        flag = "KEEP " if keep else "drop "
        print(f"{name:6} {h[:42]:42} {flag} {bucket}")
    print()
'''),
        md("""
**Reading it.** A heading that states a *conclusion* must land in **Dropped**; an *observation*
heading should map to one of `_INPUT_SECTIONS`. Watch for two failure modes:

- **Leak risk** — a conclusion/chrome heading that gets KEPT (any heading the rules don't recognise
  defaults to *Technical Details*). Empirically, CCCS *"Tactics, techniques, and procedures (TTP)"*,
  *"Suggested actions"* and *"Audience"* are all **kept** today — the first is a conclusion section
  that would leak its TTP narrative into the inputs, the other two are chrome. Only CCCS *"MITRE
  ATT&CK techniques"*, *"Purpose"* and *"References"* drop correctly. Fixing CCCS means adding
  `tactics`/`ttp`/`suggested action`/`audience` to `_DROP_KEYWORDS`. (The NCSC set, and the
  indicative ACSC set, classify cleanly — every conclusion/chrome heading already drops.)
- **Lost evidence** — an observation heading wrongly dropped (none observed here).

So the generic parser transfers structurally, but **each source needs a small drop-keyword /
body-container patch** before its conclusions are reliably withheld. That patch is the real cost
of adding a source.
"""),
        md("## 3. Optional live structural probe\nRe-fetch one sample advisory per source and run the generic body parser + label regexes over it. Off by default (ACSC throttles); set `SOURCE_REVIEW_PROBE=1` to enable."),
        code('''
PROBE = os.environ.get("SOURCE_REVIEW_PROBE", "0") == "1"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

if PROBE:
    import httpx
    from glokta.infrastructure.cti.connectors.report import (
        _AdvisorySectionParser, _advisory_body, _TECHNIQUE_RE, _CVE_RE)
    base = {"ACSC": "https://www.cyber.gov.au/about-us/view-all-content/alerts-and-advisories/",
            "NCSC": "https://www.ncsc.gov.uk/news/",
            "CCCS": "https://www.cyber.gc.ca/en/alerts-advisories/"}
    with httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True) as c:
        for name, s in SOURCES.items():
            url = base[name] + s["sample"]
            try:
                html = c.get(url).text
                parser = _AdvisorySectionParser(); parser.feed(_advisory_body(html))
                heads = [h for h, _ in parser.result() if h]
                techs = sorted({t.upper() for t in _TECHNIQUE_RE.findall(html)})
                cves = sorted({c.upper() for c in _CVE_RE.findall(html)})
                print(f"{name}: {len(heads)} headings, {len(techs)} ATT&CK ids, {len(cves)} CVEs")
                print("   headings:", heads[:8])
            except Exception as exc:
                print(f"{name}: fetch failed -> {type(exc).__name__} {str(exc)[:60]}")
else:
    print("probe disabled (set SOURCE_REVIEW_PROBE=1). Using captured findings from §2.")
'''),
        md("## 4. Suitability scorecard\nScore each criterion 0 (blocker) / 1 (needs work) / 2 (good). The total is a *fit* score for the ingest path, weighted toward index + fetchability + non-overlap (the things that cost connector work), since label/structure quality is uniformly high."),
        code('''
import pandas as pd

# (C1 index, C2 on-page structure, C3 labels, C4 originality/non-overlap, C5 volume, C6 fetchability)
SCORES = {
    "ACSC": {"C1 index": 0, "C2 structure": 1, "C3 labels": 2, "C4 non-overlap": 0, "C5 volume": 2, "C6 fetch": 0},
    "NCSC": {"C1 index": 1, "C2 structure": 2, "C3 labels": 2, "C4 non-overlap": 0, "C5 volume": 1, "C6 fetch": 1},
    "CCCS": {"C1 index": 2, "C2 structure": 2, "C3 labels": 2, "C4 non-overlap": 1, "C5 volume": 2, "C6 fetch": 2},
}
df = pd.DataFrame(SCORES).T
df["TOTAL"] = df.sum(axis=1)
df = df.sort_values("TOTAL", ascending=False)
print(df.to_string())
print("\\nmax possible:", 6 * 2)
print("ranking     :", " > ".join(df.index))
'''),
        md("""
## 5. Findings & recommendation

**All three publish genuinely high-quality, well-structured advisories** — ATT&CK ids, attributed
actors (with aliases), and IOCs are present on the flagship pages of every source. The differences
that matter are all about *ingest fit*, and they split the three cleanly:

- **CCCS (Canada) — adopt first.** Single-fetch index, full text on-page (no PDF), consistent
  `AL`-numbered originals, clean heading structure. The only work: dedup republished CERT-FR/CISA
  items, add `tactics`/`ttp`/`suggested action`/`audience` to `_DROP_KEYWORDS` (verified in §2:
  these three CCCS sections are currently kept and would leak), and handle the `/en/alerts/` vs
  `/en/alerts-advisories/` URL split. CVE coverage is inconsistent, so it helps ATE/
  TAA/SYN more than Forecast.
- **NCSC (UK) — secondary.** Individual `/news/<slug>` pages parse beautifully, but the HTML index
  is too shallow to enumerate a corpus (no pager, ~6 mixed news/advisory items) and many full CSAs
  are PDF-only. Worth a thin connector for the on-HTML flagship advisories once an enumeration path
  (sitemap or search API) is found.
- **ACSC (Australia) — defer.** Highest friction: the facet listing and the advisory pages are
  bot-throttled (every `httpx` fetch timed out in this review), several advisories are PDF-only, and
  the flagship items are co-sealed with CISA. Needs a headless fetcher or an undocumented JSON facet
  endpoint before it's worth the connector.

**The cross-cutting caveat — joint-advisory overlap (C4).** All three co-seal joint advisories with
CISA, so a large share of their output is *the same text already in our AA-series corpus*, under a
different slug. Ingesting those naively would (a) double-count items and (b) defeat contamination
control, since the model may have trained on the CISA version. **Before adding any of these sources,
the ingest path needs a cross-source dedup step** (content-hash or normalised-title match against
existing `cti_items`), keeping only each source's *original* national content. That dedup — plus a
per-source body-container + drop-keyword patch to `parse_advisory_page` — is the actual engineering
cost; the parser/claim-extraction cores transfer unchanged.

**Recommendation:** add **CCCS** next (best fit, lowest cost), with a content-hash dedup gate;
treat **NCSC** as a follow-on pending an enumeration path; **defer ACSC** until a non-throttled
fetch route exists.
"""),
    ])


def main():
    out = os.path.abspath(OUT_DIR)
    os.makedirs(out, exist_ok=True)
    notebooks = {
        "01_rcm_cve_to_cwe.ipynb": rcm_nb(),
        "02_vsp_cve_to_cvss.ipynb": vsp_nb(),
        "03_ate_report_to_techniques.ipynb": ate_nb(),
        "04_taa_narrative_to_actor.ipynb": taa_nb(),
        "05_forecast_cve_exploitation.ipynb": forecast_nb(),
        "06_syn_analysis_synthesis.ipynb": syn_nb(),
        "07_syn_pilot_recent_advisories.ipynb": syn_pilot_nb(),
        "08_source_review_national_certs.ipynb": source_review_nb(),
    }
    for name, nb in notebooks.items():
        path = os.path.join(out, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
        print("wrote", path)


if __name__ == "__main__":
    main()
